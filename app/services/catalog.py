import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Product, VectorSyncJob
from app.schemas import ProductForm
from app.services.mesh_gateway import MeshGateway
from app.services.vector_store import ChromaProductStore, product_document


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:120] or "course"


class CatalogService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _unique_slug(self, title: str, current_id: str | None = None) -> str:
        base = slugify(title)
        candidate = base
        suffix = 2
        while True:
            existing = self.db.scalar(select(Product).where(Product.slug == candidate))
            if not existing or existing.id == current_id:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    @staticmethod
    def _apply_form(product: Product, form: ProductForm) -> None:
        import json

        product.title = form.title.strip()
        product.description = form.description.strip()
        product.category = form.category.strip()
        product.level = form.level
        product.price = form.price
        product.duration_minutes = form.duration_minutes
        normalized_tags = {tag.strip().lower() for tag in form.tags if tag.strip()}
        product.tags_json = json.dumps(sorted(normalized_tags))
        product.accent = form.accent

    def _queue_sync(self, product: Product, operation: str) -> VectorSyncJob:
        job = VectorSyncJob(
            product_id=product.id,
            operation=operation,
            product_version=product.vector_version,
        )
        product.vector_status = "pending"
        self.db.add(job)
        return job

    def create(self, form: ProductForm) -> tuple[Product, VectorSyncJob]:
        product = Product(slug=self._unique_slug(form.title))
        self._apply_form(product, form)
        self.db.add(product)
        self.db.flush()
        job = self._queue_sync(product, "upsert")
        self.db.commit()
        return product, job

    def update(self, product: Product, form: ProductForm) -> VectorSyncJob:
        self._apply_form(product, form)
        product.slug = self._unique_slug(form.title, current_id=product.id)
        product.vector_version += 1
        job = self._queue_sync(product, "upsert")
        self.db.commit()
        return job

    def delete(self, product: Product) -> VectorSyncJob:
        product.is_active = False
        product.deleted_at = datetime.now(UTC)
        product.vector_version += 1
        job = self._queue_sync(product, "delete")
        self.db.commit()
        return job


class VectorSyncService:
    def __init__(
        self,
        settings: Settings | None = None,
        gateway: MeshGateway | None = None,
        vector_store: ChromaProductStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.gateway = gateway or MeshGateway(self.settings)
        self.vector_store = vector_store or ChromaProductStore(self.settings.chroma_path)

    def process_job(self, job_id: str) -> bool:
        with SessionLocal() as db:
            job = db.get(VectorSyncJob, job_id)
            if not job or job.status in {"completed", "superseded"}:
                return True
            product = db.get(Product, job.product_id)
            if product and product.vector_version > job.product_version:
                job.status = "superseded"
                job.processed_at = datetime.now(UTC)
                db.commit()
                return True
            if not self.gateway.configured and job.operation == "upsert":
                return False
            try:
                if job.operation == "delete" or not product or not product.is_active:
                    self.vector_store.delete(job.product_id)
                else:
                    embedding = self.gateway.embed([product_document(product)])[0]
                    self.vector_store.upsert(product, embedding)
                job.status = "completed"
                job.last_error = None
                job.processed_at = datetime.now(UTC)
                if product and product.vector_version == job.product_version:
                    product.vector_status = "synced"
                db.commit()
                return True
            except Exception as exc:
                job.attempts += 1
                job.status = "failed" if job.attempts >= 5 else "pending"
                job.last_error = str(exc)[:1000]
                if product and product.vector_version == job.product_version:
                    product.vector_status = "error" if job.attempts >= 5 else "pending"
                db.commit()
                return False

    def process_pending(self, limit: int = 50) -> dict[str, int]:
        with SessionLocal() as db:
            jobs = list(
                db.scalars(
                    select(VectorSyncJob)
                    .where(VectorSyncJob.status.in_(["pending", "failed"]))
                    .order_by(VectorSyncJob.created_at)
                    .limit(limit)
                )
            )
            found = len(jobs)
            completed = 0
            upserts: list[tuple[VectorSyncJob, Product]] = []

            for job in jobs:
                product = db.get(Product, job.product_id)
                if product and product.vector_version > job.product_version:
                    job.status = "superseded"
                    job.processed_at = datetime.now(UTC)
                    completed += 1
                elif job.operation == "delete" or not product or not product.is_active:
                    try:
                        self.vector_store.delete(job.product_id)
                        job.status = "completed"
                        job.last_error = None
                        job.processed_at = datetime.now(UTC)
                        if product and product.vector_version == job.product_version:
                            product.vector_status = "synced"
                        completed += 1
                    except Exception as exc:
                        self._record_failure(job, product, exc)
                elif self.gateway.configured:
                    upserts.append((job, product))

            if upserts:
                try:
                    embeddings = self.gateway.embed(
                        [product_document(product) for _job, product in upserts]
                    )
                    for (job, product), embedding in zip(upserts, embeddings, strict=True):
                        self.vector_store.upsert(product, embedding)
                        job.status = "completed"
                        job.last_error = None
                        job.processed_at = datetime.now(UTC)
                        if product.vector_version == job.product_version:
                            product.vector_status = "synced"
                        completed += 1
                except Exception as exc:
                    for job, product in upserts:
                        self._record_failure(job, product, exc)
            db.commit()
        return {"found": found, "completed": completed, "remaining": found - completed}

    @staticmethod
    def _record_failure(job: VectorSyncJob, product: Product | None, exc: Exception) -> None:
        job.attempts += 1
        job.status = "failed" if job.attempts >= 5 else "pending"
        job.last_error = str(exc)[:1000]
        if product and product.vector_version == job.product_version:
            product.vector_status = "error" if job.attempts >= 5 else "pending"

    def status(self) -> dict[str, object]:
        try:
            return {"available": True, "documents": self.vector_store.count()}
        except Exception as exc:
            return {"available": False, "documents": 0, "error": str(exc)[:160]}

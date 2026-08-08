from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Product, VectorSyncJob
from app.schemas import ProductForm
from app.services import catalog as catalog_module
from app.services.catalog import CatalogService, VectorSyncService


def product_form(title="Reliable Agents"):
    return ProductForm(
        title=title,
        description="Build reliable AI agents with planning, tools, tests, and evaluation gates.",
        category="Agentic AI",
        level="Advanced",
        price=Decimal("129.00"),
        duration_minutes=480,
        tags=["agents", "evaluation"],
        accent="violet",
    )


def test_product_create_and_update_always_queue_vector_version(db):
    service = CatalogService(db)
    product, created_job = service.create(product_form())

    assert product.vector_status == "pending"
    assert created_job.operation == "upsert"
    assert created_job.product_version == 1

    update_job = service.update(product, product_form("Reliable Agents, Advanced"))
    jobs = list(db.scalars(select(VectorSyncJob).order_by(VectorSyncJob.product_version)))

    assert product.vector_version == 2
    assert update_job.product_version == 2
    assert len(jobs) == 2


def test_product_delete_is_soft_and_queues_vector_delete(db):
    service = CatalogService(db)
    product, _ = service.create(product_form())

    job = service.delete(product)

    assert db.get(Product, product.id) is not None
    assert product.is_active is False
    assert job.operation == "delete"
    assert job.product_version == 2


def test_vector_worker_uses_mesh_embedding_and_marks_job_synced(db, monkeypatch, tmp_path):
    product, job = CatalogService(db).create(product_form())

    class FakeGateway:
        configured = True

        def embed(self, texts):
            assert product.title in texts[0]
            return [[0.1, 0.2, 0.3]]

    class FakeStore:
        def __init__(self):
            self.upserted = []

        def upsert(self, product_to_sync, embedding):
            self.upserted.append((product_to_sync.id, embedding))

    worker_sessions = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(catalog_module, "SessionLocal", worker_sessions)
    store = FakeStore()
    service = VectorSyncService(
        settings=Settings(mesh_api_key="rsk_test", chroma_path=tmp_path),
        gateway=FakeGateway(),
        vector_store=store,
    )

    assert service.process_job(job.id) is True
    db.expire_all()
    assert db.get(Product, product.id).vector_status == "synced"
    assert db.get(VectorSyncJob, job.id).status == "completed"
    assert store.upserted == [(product.id, [0.1, 0.2, 0.3])]

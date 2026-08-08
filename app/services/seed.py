import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Product
from app.schemas import ProductForm
from app.services.catalog import CatalogService, VectorSyncService


def seed_catalog(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.seed_demo_data:
        return 0
    with SessionLocal() as db:
        catalog_path = Path(__file__).resolve().parents[2] / "data" / "catalog.json"
        records = json.loads(catalog_path.read_text(encoding="utf-8"))
        service = CatalogService(db)
        created = 0
        for record in records:
            try:
                if db.scalar(select(Product.id).where(Product.title == record.get("title"))):
                    continue
                service.create(ProductForm.model_validate(record))
                created += 1
            except ValidationError:
                continue
    if created and settings.mesh_configured:
        VectorSyncService(settings).process_pending(limit=created)
    return created

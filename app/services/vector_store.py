import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from app.models import Product


@dataclass(slots=True)
class VectorHit:
    product_id: str
    score: float
    document: str
    metadata: dict[str, Any]


def product_document(product: Product) -> str:
    tags = ", ".join(json.loads(product.tags_json or "[]"))
    return (
        f"Course: {product.title}\n"
        f"Category: {product.category}\n"
        f"Level: {product.level}\n"
        f"Topics: {tags}\n"
        f"Description: {product.description}"
    )


class ChromaProductStore:
    COLLECTION_NAME = "lumalearn_products"

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        Path(self.path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.path)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "purpose": "catalog-grounded recommendations"},
        )

    def upsert(self, product: Product, embedding: list[float]) -> None:
        self.collection.upsert(
            ids=[product.id],
            embeddings=[embedding],
            documents=[product_document(product)],
            metadatas=[
                {
                    "title": product.title,
                    "category": product.category,
                    "level": product.level,
                    "version": product.vector_version,
                    "active": product.is_active,
                }
            ],
        )

    def delete(self, product_id: str) -> None:
        self.collection.delete(ids=[product_id])

    def query(self, embedding: list[float], limit: int = 8) -> list[VectorHit]:
        count = self.collection.count()
        if count == 0:
            return []
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(limit, count),
            include=["metadatas", "documents", "distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            VectorHit(
                product_id=product_id,
                score=max(0.0, min(1.0, 1.0 - float(distance))),
                document=document or "",
                metadata=metadata or {},
            )
            for product_id, document, metadata, distance in zip(
                ids, documents, metadatas, distances, strict=True
            )
        ]

    def count(self) -> int:
        return self.collection.count()

    def has_product(self, product_id: str) -> bool:
        return bool(self.collection.get(ids=[product_id], include=[]).get("ids"))

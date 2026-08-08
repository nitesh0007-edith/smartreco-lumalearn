import hashlib
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ActivityEvent, Product, User
from app.schemas import EventBatchIn

EVENT_WEIGHTS = {
    "catalog_view": 0.5,
    "category_view": 1.0,
    "product_view": 2.0,
    "product_click": 3.0,
    "recommendation_click": 4.0,
    "search": 4.0,
    "dwell": 1.0,
    "cart_add": 3.5,
    "purchase": 5.0,
}


def record_event_batch(db: Session, user_id: int, batch: EventBatchIn) -> dict[str, int]:
    client_ids = [event.client_event_id for event in batch.events]
    requested_product_ids = {event.product_id for event in batch.events if event.product_id}
    valid_product_ids = set(
        db.scalars(
            select(Product.id).where(
                Product.id.in_(requested_product_ids), Product.is_active.is_(True)
            )
        )
    )
    existing = set(
        db.scalars(
            select(ActivityEvent.client_event_id).where(
                ActivityEvent.user_id == user_id,
                ActivityEvent.client_event_id.in_(client_ids),
            )
        )
    )
    accepted = 0
    now = datetime.now(UTC)
    for event in batch.events:
        if event.client_event_id in existing:
            continue
        created_at = event.created_at
        if created_at > now + timedelta(minutes=5):
            created_at = now
        metadata = {
            str(key)[:80]: value
            for key, value in list(event.metadata.items())[:20]
            if isinstance(value, str | int | float | bool | None)
        }
        db.add(
            ActivityEvent(
                user_id=user_id,
                product_id=event.product_id if event.product_id in valid_product_ids else None,
                client_event_id=event.client_event_id,
                event_type=event.event_type,
                path=event.path,
                query=event.query.strip() if event.query else None,
                duration_ms=event.duration_ms,
                metadata_json=json.dumps(metadata),
                created_at=created_at,
            )
        )
        accepted += 1
    db.commit()
    return {"accepted": accepted, "duplicates": len(batch.events) - accepted}


def build_behavior_profile(db: Session, user_id: int, limit: int = 120) -> dict[str, Any]:
    user = db.get(User, user_id)
    target_role = user.target_role if user else None
    cutoff = datetime.now(UTC) - timedelta(days=30)
    events = list(
        db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.user_id == user_id, ActivityEvent.created_at >= cutoff)
            .options(selectinload(ActivityEvent.product))
            .order_by(ActivityEvent.id.desc())
            .limit(limit)
        )
    )
    events.reverse()
    categories: Counter[str] = Counter()
    topics: Counter[str] = Counter()
    searches: Counter[str] = Counter()
    viewed_product_ids: set[str] = set()
    cart_product_ids: set[str] = set()
    purchased_product_ids: set[str] = set()
    total_weight = 0.0

    for event in events:
        weight = EVENT_WEIGHTS.get(event.event_type, 0.25)
        if event.event_type == "dwell" and event.duration_ms:
            weight = min(5.0, max(0.5, event.duration_ms / 30_000))
        total_weight += weight
        if event.query:
            query = " ".join(event.query.lower().split())
            searches[query] += weight
            for word in query.split():
                if len(word) > 2:
                    topics[word] += weight
        if event.product:
            viewed_product_ids.add(event.product.id)
            if event.event_type == "cart_add":
                cart_product_ids.add(event.product.id)
            elif event.event_type == "purchase":
                purchased_product_ids.add(event.product.id)
            categories[event.product.category] += weight
            for tag in json.loads(event.product.tags_json or "[]"):
                topics[tag] += weight

    top_categories = [name for name, _score in categories.most_common(4)]
    top_topics = [name for name, _score in topics.most_common(8)]
    top_searches = [name for name, _score in searches.most_common(5)]
    parts = []
    if top_categories:
        parts.append(f"Strongest course categories: {', '.join(top_categories)}.")
    if top_topics:
        parts.append(f"Repeated topics and skills: {', '.join(top_topics)}.")
    if top_searches:
        parts.append(f"Recent searches: {', '.join(top_searches)}.")
    if target_role:
        parts.append(f"Learner's stated target role: {target_role}.")
    if cart_product_ids:
        parts.append(f"Courses in the learner's cart: {len(cart_product_ids)}.")
    if purchased_product_ids:
        parts.append(
            f"Previously purchased courses: {len(purchased_product_ids)}; "
            "recommend complementary next steps."
        )
    parts.append(
        f"Signal base: {len(events)} meaningful events with weighted intent {total_weight:.1f}."
    )
    max_id = max((event.id for event in events), default=0)
    fingerprint_input = "|".join(
        ":".join(
            [
                str(event.id),
                event.event_type,
                event.product_id or "",
                event.query or "",
                str(event.duration_ms or 0),
            ]
        )
        for event in events
    )
    fingerprint = hashlib.sha256(
        f"role:{target_role or ''}|{fingerprint_input}".encode()
    ).hexdigest()
    query_terms = (
        ([target_role] if target_role else []) + top_searches + top_categories + top_topics
    )
    query_text = "Learning goals and course interests: " + "; ".join(query_terms[:14])
    return {
        "event_count": len(events),
        "max_event_id": max_id,
        "fingerprint": fingerprint,
        "total_weight": total_weight,
        "top_categories": top_categories,
        "top_topics": top_topics,
        "top_searches": top_searches,
        "viewed_product_ids": sorted(viewed_product_ids),
        "cart_product_ids": sorted(cart_product_ids),
        "purchased_product_ids": sorted(purchased_product_ids),
        "summary": " ".join(parts),
        "query_text": query_text,
        "target_role": target_role,
    }

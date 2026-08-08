from datetime import UTC, datetime

from app.models import ActivityEvent, Product, User
from app.security import hash_password
from app.services.behavior import build_behavior_profile


def test_behavior_profile_weights_search_and_product_topics(db):
    user = User(name="Ada", email="ada@example.com", password_hash=hash_password("long-password"))
    product = Product(
        title="Agentic Systems",
        slug="agentic-systems",
        description="A sufficiently detailed description of reliable agent systems.",
        category="Agentic AI",
        level="Advanced",
        price=99,
        duration_minutes=300,
        tags_json='["agents", "planning"]',
    )
    db.add_all([user, product])
    db.flush()
    db.add_all(
        [
            ActivityEvent(
                user_id=user.id,
                product_id=product.id,
                client_event_id="event-0001",
                event_type="product_view",
                path="/products/agentic-systems",
                metadata_json="{}",
                created_at=datetime.now(UTC),
            ),
            ActivityEvent(
                user_id=user.id,
                product_id=None,
                client_event_id="event-0002",
                event_type="search",
                path="/?q=agent+planning",
                query="agent planning",
                metadata_json="{}",
                created_at=datetime.now(UTC),
            ),
        ]
    )
    db.commit()

    profile = build_behavior_profile(db, user.id)

    assert profile["event_count"] == 2
    assert profile["top_categories"] == ["Agentic AI"]
    assert "planning" in profile["top_topics"]
    assert profile["top_searches"] == ["agent planning"]
    assert profile["total_weight"] == 6.0
    assert len(profile["fingerprint"]) == 64

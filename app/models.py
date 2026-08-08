import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    target_role: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["ActivityEvent"]] = relationship(back_populates="user")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="user")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(180), index=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), index=True)
    level: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    accent: Mapped[str] = mapped_column(String(20), default="violet")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    vector_version: Mapped[int] = mapped_column(Integer, default=1)
    vector_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["ActivityEvent"]] = relationship(back_populates="product")


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
    product: Mapped[Product] = relationship()


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (Index("ix_purchases_user_created", "user_id", "purchased_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    discount_code: Mapped[str | None] = mapped_column(String(40))
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
    product: Mapped[Product] = relationship()


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        UniqueConstraint("user_id", "client_event_id", name="uq_event_user_client"),
        Index("ix_events_user_created", "user_id", "created_at"),
        Index("ix_events_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    client_event_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    path: Mapped[str] = mapped_column(String(500))
    query: Mapped[str | None] = mapped_column(String(300))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="events")
    product: Mapped[Product | None] = relationship(back_populates="events")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_fingerprint", name="uq_rec_user_fingerprint"),
        Index("ix_recommendations_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    headline: Mapped[str] = mapped_column(String(180))
    narrative: Mapped[str] = mapped_column(Text)
    interest_summary: Mapped[str] = mapped_column(Text)
    activity_fingerprint: Mapped[str] = mapped_column(String(64))
    source_event_max_id: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="recommendations")
    items: Mapped[list["RecommendationItem"]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
        order_by="RecommendationItem.rank",
    )


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"
    __table_args__ = (UniqueConstraint("recommendation_id", "rank", name="uq_rec_rank"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    retrieval_score: Mapped[float] = mapped_column(Numeric(7, 6))

    recommendation: Mapped[Recommendation] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class VectorSyncJob(Base):
    __tablename__ = "vector_sync_jobs"
    __table_args__ = (
        UniqueConstraint("product_id", "product_version", name="uq_vector_product_version"),
        Index("ix_vector_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(String(36), index=True)
    operation: Mapped[str] = mapped_column(String(20))
    product_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_user_started", "user_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    trace_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")
    decision: Mapped[str | None] = mapped_column(String(80))
    activity_fingerprint: Mapped[str | None] = mapped_column(String(64))
    node_trace_json: Mapped[str] = mapped_column(Text, default="[]")
    model: Mapped[str | None] = mapped_column(String(150))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DigestDelivery(Base):
    __tablename__ = "digest_deliveries"
    __table_args__ = (UniqueConstraint("user_id", "recommendation_id", name="uq_digest_user_rec"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[str] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="email")
    status: Mapped[str] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


JsonObject = dict[str, Any]

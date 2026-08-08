from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

EventType = Literal[
    "catalog_view",
    "category_view",
    "product_view",
    "product_click",
    "recommendation_click",
    "search",
    "dwell",
    "cart_add",
    "purchase",
]


class EventIn(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=36)
    event_type: EventType
    product_id: str | None = Field(default=None, max_length=36)
    path: str = Field(max_length=500)
    query: str | None = Field(default=None, max_length=300)
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class EventBatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=50)
    csrf_token: str = Field(min_length=16, max_length=128)


class ProductForm(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=20, max_length=5000)
    category: str = Field(min_length=2, max_length=80)
    level: Literal["Beginner", "Intermediate", "Advanced", "All levels"]
    price: Decimal = Field(ge=0, le=100_000)
    duration_minutes: int = Field(ge=15, le=100_000)
    tags: list[str] = Field(min_length=1, max_length=20)
    accent: Literal["violet", "cyan", "amber", "rose", "lime", "blue"] = "violet"


class RegistrationForm(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    target_role: str | None = Field(default=None, max_length=80)


class LoginForm(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordResetForm(BaseModel):
    password: str = Field(min_length=10, max_length=128)


class MeshRecommendationItem(BaseModel):
    product_id: str
    reason: str = Field(min_length=15, max_length=350)


class MeshRecommendation(BaseModel):
    headline: str = Field(min_length=8, max_length=180)
    narrative: str = Field(min_length=40, max_length=1200)
    interest_summary: str = Field(min_length=20, max_length=500)
    recommendations: list[MeshRecommendationItem] = Field(min_length=1, max_length=4)

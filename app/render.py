import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.models import User
from app.security import ensure_csrf_token, pop_flashes

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def money(value: Decimal | float | str) -> str:
    amount = Decimal(str(value))
    return "Free" if amount == 0 else f"£{amount:,.0f}"


def duration(minutes: int) -> str:
    hours = minutes / 60
    return f"{hours:.1f} hours" if hours % 1 else f"{int(hours)} hours"


def tags(value: str) -> list[str]:
    try:
        return list(json.loads(value))
    except (TypeError, json.JSONDecodeError):
        return []


def relative_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - value).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


templates.env.filters.update(
    {"money": money, "duration": duration, "tags": tags, "relative_time": relative_time}
)


def render(
    request: Request,
    template_name: str,
    *,
    user: User | None = None,
    status_code: int = 200,
    **context: Any,
):  # type: ignore[no-untyped-def]
    payload = {
        "request": request,
        "user": user,
        "csrf_token": ensure_csrf_token(request),
        "flashes": pop_flashes(request),
        "settings": get_settings(),
        **context,
    }
    return templates.TemplateResponse(request, template_name, payload, status_code=status_code)

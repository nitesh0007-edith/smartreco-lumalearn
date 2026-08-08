import hmac
import secrets
from typing import Any

from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def validate_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not submitted or not expected or not hmac.compare_digest(str(submitted), str(expected)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def add_flash(request: Request, message: str, category: str = "info") -> None:
    flashes: list[dict[str, Any]] = request.session.setdefault("flashes", [])
    flashes.append({"message": message, "category": category})
    request.session["flashes"] = flashes[-4:]


def pop_flashes(request: Request) -> list[dict[str, Any]]:
    return request.session.pop("flashes", [])

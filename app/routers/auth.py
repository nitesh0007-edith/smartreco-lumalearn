import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import optional_user
from app.models import PasswordResetToken, User
from app.render import render
from app.schemas import LoginForm, PasswordResetForm, RegistrationForm
from app.security import add_flash, ensure_csrf_token, hash_password, validate_csrf, verify_password

router = APIRouter(tags=["authentication"])


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    field = str(error["loc"][-1]).replace("_", " ").capitalize()
    return f"{field}: {error['msg']}"


@router.get("/register")
def register_page(request: Request, user: User | None = Depends(optional_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/register.html", page_title="Create account")


@router.post("/register")
async def register(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        payload = RegistrationForm(
            name=str(form.get("name", "")),
            email=str(form.get("email", "")),
            password=str(form.get("password", "")),
            target_role=str(form.get("target_role", "")) or None,
        )
    except ValidationError as exc:
        return render(
            request,
            "auth/register.html",
            page_title="Create account",
            error=_first_error(exc),
            values={"name": form.get("name", ""), "email": form.get("email", "")},
            status_code=422,
        )
    email = str(payload.email).lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        return render(
            request,
            "auth/register.html",
            page_title="Create account",
            error="An account with that email already exists.",
            values={"name": payload.name, "email": email},
            status_code=409,
        )
    user = User(
        name=payload.name.strip(),
        email=email,
        password_hash=hash_password(payload.password),
        target_role=payload.target_role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(
            request,
            "auth/register.html",
            page_title="Create account",
            error="An account with that email already exists.",
            values={"name": payload.name, "email": email},
            status_code=409,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    ensure_csrf_token(request)
    add_flash(request, f"Welcome, {user.name}. Your learning signal starts here.", "success")
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(optional_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/login.html", page_title="Sign in")


@router.get("/forgot-password")
def forgot_password_page(request: Request, user: User | None = Depends(optional_user)):
    if user:
        return RedirectResponse("/profile", status_code=303)
    return render(request, "auth/forgot_password.html", page_title="Reset password")


@router.post("/forgot-password")
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    email = str(form.get("email", "")).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email)) if email else None
    reset_link = None
    if user:
        raw_token = secrets.token_urlsafe(32)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        db.commit()
        # The local demo has no SMTP credentials; expose the one-time link in the UI.
        reset_link = f"/reset-password?token={raw_token}"
    return render(
        request,
        "auth/forgot_password.html",
        page_title="Reset password",
        submitted=True,
        reset_link=reset_link,
    )


def _reset_token(db: Session, raw_token: str) -> PasswordResetToken | None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    expires_at = token.expires_at if token else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if not token or token.used_at or (expires_at and expires_at < datetime.now(UTC)):
        return None
    return token


@router.get("/reset-password")
def reset_password_page(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if user:
        return RedirectResponse("/profile", status_code=303)
    valid = bool(_reset_token(db, token))
    return render(
        request,
        "auth/reset_password.html",
        page_title="Choose a new password",
        token=token,
        valid=valid,
    )


@router.post("/reset-password")
async def reset_password(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    raw_token = str(form.get("token", ""))
    token = _reset_token(db, raw_token)
    if not token:
        return render(
            request,
            "auth/reset_password.html",
            page_title="Choose a new password",
            token=raw_token,
            valid=False,
            error="This reset link is invalid or has expired.",
            status_code=400,
        )
    try:
        payload = PasswordResetForm(password=str(form.get("password", "")))
    except ValidationError as exc:
        return render(
            request,
            "auth/reset_password.html",
            page_title="Choose a new password",
            token=raw_token,
            valid=True,
            error=_first_error(exc),
            status_code=422,
        )
    token.user.password_hash = hash_password(payload.password)
    token.used_at = datetime.now(UTC)
    db.commit()
    add_flash(request, "Password updated. You can sign in with your new password.", "success")
    return RedirectResponse("/login", status_code=303)


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    try:
        payload = LoginForm(
            email=str(form.get("email", "")), password=str(form.get("password", ""))
        )
    except ValidationError:
        payload = None
    user = None
    if payload:
        user = db.scalar(select(User).where(func.lower(User.email) == str(payload.email).lower()))
    if not payload or not user or not verify_password(payload.password, user.password_hash):
        return render(
            request,
            "auth/login.html",
            page_title="Sign in",
            error="Email or password is incorrect.",
            values={"email": form.get("email", "")},
            status_code=401,
        )
    if not user.is_active:
        return render(
            request,
            "auth/login.html",
            page_title="Sign in",
            error="This account is inactive.",
            status_code=403,
        )
    user.last_login_at = datetime.now(UTC)
    db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    ensure_csrf_token(request)
    add_flash(request, f"Good to see you, {user.name}.", "success")
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    form = await request.form()
    validate_csrf(request, form.get("csrf_token"))
    request.session.clear()
    return RedirectResponse("/", status_code=303)

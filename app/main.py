from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import init_db
from app.routers import admin, api, auth, web
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.seed import seed_catalog

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.validate_runtime()
    init_db()
    seed_catalog(settings)
    start_scheduler(settings)
    yield
    stop_scheduler()


app = FastAPI(
    title="LumaLearn SmartReco",
    description="Behavior-aware, Mesh-powered course recommendations grounded in a live catalog.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="lumalearn_session",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=settings.cookie_secure or settings.is_production,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(web.router)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(404)
async def not_found(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "That page was not found."}, status_code=404)

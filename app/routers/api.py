from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import current_user
from app.models import User
from app.schemas import EventBatchIn
from app.security import validate_csrf
from app.services.behavior import record_event_batch
from app.services.catalog import VectorSyncService
from app.services.recommendations import RecommendationService, latest_recommendation

router = APIRouter(prefix="/api", tags=["api"])


def refresh_for_user(user_id: int, trigger: str = "event_batch", force: bool = False) -> None:
    RecommendationService().maybe_refresh(user_id, trigger=trigger, force=force)


@router.post("/events/batch", status_code=202)
def ingest_events(
    batch: EventBatchIn,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    validate_csrf(request, batch.csrf_token or request.headers.get("x-csrf-token"))
    result = record_event_batch(db, user.id, batch)
    if result["accepted"]:
        background.add_task(refresh_for_user, user.id)
    return {"status": "accepted", **result}


@router.get("/recommendations/current")
def current_recommendation(db: Session = Depends(get_db), user: User = Depends(current_user)):
    recommendation = latest_recommendation(db, user.id)
    if not recommendation:
        return {"recommendation": None}
    return {
        "recommendation": {
            "id": recommendation.id,
            "headline": recommendation.headline,
            "narrative": recommendation.narrative,
            "interest_summary": recommendation.interest_summary,
            "created_at": recommendation.created_at,
            "items": [
                {
                    "rank": item.rank,
                    "reason": item.reason,
                    "retrieval_score": float(item.retrieval_score),
                    "product": {
                        "id": item.product.id,
                        "slug": item.product.slug,
                        "title": item.product.title,
                        "category": item.product.category,
                    },
                }
                for item in recommendation.items
            ],
        }
    }


@router.post("/recommendations/refresh", status_code=202)
async def request_refresh(
    request: Request,
    background: BackgroundTasks,
    user: User = Depends(current_user),
):
    data = await request.json()
    validate_csrf(request, request.headers.get("x-csrf-token") or data.get("csrf_token"))
    background.add_task(refresh_for_user, user.id, "user_request", True)
    return {"status": "queued"}


@router.post("/profile/digest")
async def set_digest_preference(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    data = await request.json()
    validate_csrf(request, request.headers.get("x-csrf-token") or data.get("csrf_token"))
    user.digest_opt_in = bool(data.get("enabled"))
    db.commit()
    return {"enabled": user.digest_opt_in}


@router.get("/health")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    db.execute(text("SELECT 1"))
    vector = VectorSyncService(settings).status()
    healthy = bool(vector["available"])
    return {
        "status": "ok" if healthy else "degraded",
        "database": "ok",
        "vector_store": vector,
        "mesh": {
            "configured": settings.mesh_configured,
            "gateway": settings.mesh_base_url,
            "chat_model": settings.mesh_chat_model,
            "embedding_model": settings.mesh_embedding_model,
        },
    }

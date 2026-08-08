import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import ActivityEvent, AgentRun, Recommendation, RecommendationItem
from app.services.agent import RecommendationAgent
from app.services.behavior import build_behavior_profile
from app.services.mesh_gateway import MeshNotConfiguredError


@dataclass(slots=True)
class RefreshResult:
    status: str
    reason: str
    recommendation_id: str | None = None


class RecommendationService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def maybe_refresh(
        self, user_id: int, *, trigger: str = "event_batch", force: bool = False
    ) -> RefreshResult:
        with SessionLocal() as db:
            profile = build_behavior_profile(db, user_id)
            if profile["event_count"] == 0:
                return RefreshResult("skipped", "no_activity")

            latest = db.scalar(
                select(Recommendation)
                .where(Recommendation.user_id == user_id)
                .order_by(Recommendation.created_at.desc())
                .limit(1)
            )
            source_watermark = latest.source_event_max_id if latest else 0
            events_since = (
                db.scalar(
                    select(func.count(ActivityEvent.id)).where(
                        ActivityEvent.user_id == user_id,
                        ActivityEvent.id > source_watermark,
                    )
                )
                or 0
            )
            high_intent = bool(profile["top_searches"]) and events_since >= 2
            below_threshold = events_since < self.settings.recommendation_event_threshold
            if not force and below_threshold and not high_intent:
                return RefreshResult("skipped", "event_threshold")
            latest_created_at = latest.created_at if latest else None
            if latest_created_at and latest_created_at.tzinfo is None:
                latest_created_at = latest_created_at.replace(tzinfo=UTC)
            if (
                not force
                and latest_created_at
                and datetime.now(UTC) - latest_created_at
                < timedelta(minutes=self.settings.recommendation_cooldown_minutes)
            ):
                return RefreshResult("skipped", "cooldown")
            if latest and latest.activity_fingerprint == profile["fingerprint"]:
                return RefreshResult("cached", "unchanged_behavior", latest.id)

            latest_run = db.scalar(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            )
            latest_run_at = latest_run.started_at if latest_run else None
            if latest_run_at and latest_run_at.tzinfo is None:
                latest_run_at = latest_run_at.replace(tzinfo=UTC)
            if (
                not force
                and latest_run_at
                and datetime.now(UTC) - latest_run_at
                < timedelta(seconds=self.settings.agent_retry_cooldown_seconds)
            ):
                return RefreshResult("skipped", "agent_retry_cooldown")

            run = AgentRun(
                user_id=user_id,
                trigger=trigger,
                activity_fingerprint=profile["fingerprint"],
            )
            db.add(run)
            db.commit()
            try:
                state = RecommendationAgent(db, settings=self.settings).run(
                    user_id=user_id,
                    trace_id=run.trace_id,
                    trigger=trigger,
                    profile=profile,
                )
                run.node_trace_json = json.dumps(state.get("node_trace", []))
                run.decision = state.get("decision")
                if not state.get("result"):
                    run.status = "skipped"
                    run.finished_at = datetime.now(UTC)
                    db.commit()
                    return RefreshResult("skipped", state.get("decision", "no_candidates"))

                result = state["result"]
                for old in db.scalars(
                    select(Recommendation).where(
                        Recommendation.user_id == user_id, Recommendation.status == "active"
                    )
                ):
                    old.status = "superseded"
                recommendation = Recommendation(
                    user_id=user_id,
                    headline=result["headline"],
                    narrative=result["narrative"],
                    interest_summary=result["interest_summary"],
                    activity_fingerprint=profile["fingerprint"],
                    source_event_max_id=profile["max_event_id"],
                    trigger=trigger,
                    model=state.get("model", self.settings.mesh_chat_model),
                    expires_at=datetime.now(UTC)
                    + timedelta(hours=self.settings.recommendation_ttl_hours),
                )
                db.add(recommendation)
                db.flush()
                for rank, item in enumerate(result["items"], start=1):
                    db.add(
                        RecommendationItem(
                            recommendation_id=recommendation.id,
                            product_id=item["product_id"],
                            rank=rank,
                            reason=item["reason"],
                            retrieval_score=item["retrieval_score"],
                        )
                    )
                run.status = "completed"
                run.model = state.get("model")
                run.prompt_tokens = state.get("prompt_tokens", 0)
                run.completion_tokens = state.get("completion_tokens", 0)
                run.finished_at = datetime.now(UTC)
                db.commit()
                return RefreshResult("created", "behavior_changed", recommendation.id)
            except IntegrityError:
                db.rollback()
                duplicate = db.scalar(
                    select(Recommendation).where(
                        Recommendation.user_id == user_id,
                        Recommendation.activity_fingerprint == profile["fingerprint"],
                    )
                )
                return RefreshResult(
                    "cached", "concurrent_refresh", duplicate.id if duplicate else None
                )
            except MeshNotConfiguredError as exc:
                run = db.get(AgentRun, run.id)
                if run:
                    run.status = "failed"
                    run.error = str(exc)
                    run.finished_at = datetime.now(UTC)
                    db.commit()
                return RefreshResult("unavailable", "mesh_not_configured")
            except Exception as exc:
                db.rollback()
                run = db.get(AgentRun, run.id)
                if run:
                    run.status = "failed"
                    run.error = f"{type(exc).__name__}: {str(exc)[:900]}"
                    run.finished_at = datetime.now(UTC)
                    db.commit()
                return RefreshResult("failed", "agent_error")


def latest_recommendation(db, user_id: int) -> Recommendation | None:  # type: ignore[no-untyped-def]
    return db.scalar(
        select(Recommendation)
        .where(Recommendation.user_id == user_id, Recommendation.status == "active")
        .options(selectinload(Recommendation.items).selectinload(RecommendationItem.product))
        .order_by(Recommendation.created_at.desc())
        .limit(1)
    )

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.services.catalog import VectorSyncService
from app.services.email_digest import DigestService

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    global _scheduler
    settings = settings or get_settings()
    if not settings.scheduler_enabled or _scheduler:
        return _scheduler
    scheduler = BackgroundScheduler(timezone="UTC", daemon=True)
    scheduler.add_job(
        lambda: VectorSyncService(settings).process_pending(limit=50),
        IntervalTrigger(minutes=5),
        id="vector-outbox-reconciliation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        lambda: DigestService(settings).run_daily(),
        CronTrigger(hour=settings.digest_hour_utc, minute=0, timezone="UTC"),
        id="daily-personalized-digest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Background scheduler started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None

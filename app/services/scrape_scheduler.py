import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.time import DISPLAY_TIMEZONE
from app.db.session import engine
from app.models.common import utcnow
from app.services.notification_scheduler import scrape_and_notify
from app.services.product_scrape import run_product_scrape
from app.services.scrape_lock import scrape_lock

logger = logging.getLogger(__name__)

MIN_SLEEP_SECONDS = 1.0


@dataclass(slots=True, frozen=True)
class ScheduledRun:
    at: datetime
    jobs: bool = False
    products: bool = False


def _local_start(day: datetime, hour: int) -> datetime:
    return datetime.combine(day.date(), time(hour), tzinfo=DISPLAY_TIMEZONE)


def next_job_run(
    now: datetime,
    *,
    start_hour: int,
    end_hour: int,
    interval_seconds: int,
) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")

    local_now = now.astimezone(DISPLAY_TIMEZONE)
    start = _local_start(local_now, start_hour)
    end = _local_start(local_now, end_hour)
    interval = timedelta(seconds=interval_seconds)

    candidate = start
    while candidate <= end:
        if candidate > local_now:
            return candidate.astimezone(UTC)
        candidate += interval

    return _local_start(local_now + timedelta(days=1), start_hour).astimezone(UTC)


def next_product_run(now: datetime, *, start_hour: int) -> datetime:
    local_now = now.astimezone(DISPLAY_TIMEZONE)
    candidate = _local_start(local_now, start_hour)
    if candidate > local_now:
        return candidate.astimezone(UTC)
    return _local_start(local_now + timedelta(days=1), start_hour).astimezone(UTC)


def next_scheduled_run(now: datetime, settings: Settings) -> ScheduledRun | None:
    jobs_next = (
        next_job_run(
            now,
            start_hour=settings.scrape_active_start_hour,
            end_hour=settings.scrape_active_end_hour,
            interval_seconds=settings.scrape_interval_seconds,
        )
        if settings.scrape_interval_seconds > 0
        else None
    )
    products_next = (
        next_product_run(now, start_hour=settings.scrape_active_start_hour)
        if settings.product_scrape_interval_seconds > 0
        else None
    )

    candidates = [value for value in (jobs_next, products_next) if value is not None]
    if not candidates:
        return None

    at = min(candidates)
    return ScheduledRun(
        at=at,
        jobs=jobs_next is not None and jobs_next == at,
        products=products_next is not None and products_next == at,
    )


def in_active_window(now: datetime, settings: Settings) -> bool:
    local_now = now.astimezone(DISPLAY_TIMEZONE)
    return (
        settings.scrape_active_start_hour
        <= local_now.hour
        <= settings.scrape_active_end_hour
    )


async def _run_jobs() -> None:
    with Session(engine) as session:
        try:
            await scrape_and_notify(session)
        except Exception:
            session.rollback()
            logger.exception("Scheduled job scrape failed")
            return


async def _run_products(settings: Settings) -> None:
    with Session(engine) as session:
        try:
            await run_product_scrape(
                session,
                interval_seconds=settings.product_scrape_interval_seconds,
            )
        except Exception:
            session.rollback()
            logger.exception("Scheduled product scrape failed")
            return


async def _run_due(settings: Settings, *, jobs: bool, products: bool) -> None:
    if not jobs and not products:
        return
    if not await scrape_lock.try_acquire():
        logger.info("Skipping scheduled scrape: another scrape is running")
        return

    try:
        if jobs:
            await _run_jobs()
        if products:
            await _run_products(settings)
    finally:
        await scrape_lock.release()


async def run_scrape_scheduler(settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    jobs_enabled = settings.scrape_interval_seconds > 0
    products_enabled = settings.product_scrape_interval_seconds > 0
    if not jobs_enabled and not products_enabled:
        logger.info("Scraping scheduler disabled")
        return

    if in_active_window(utcnow(), settings):
        logger.info(
            "Scraping scheduler starting inside active window; running catch-up"
        )
        await _run_due(
            settings,
            jobs=jobs_enabled,
            products=products_enabled,
        )

    while True:
        run = next_scheduled_run(utcnow(), settings)
        if run is None:
            logger.info("Scraping scheduler disabled")
            return

        delay = max(MIN_SLEEP_SECONDS, (run.at - utcnow()).total_seconds())
        logger.info(
            "Next scheduled scrape at %s (jobs=%s products=%s)",
            run.at.astimezone(DISPLAY_TIMEZONE),
            run.jobs,
            run.products,
        )
        await asyncio.sleep(delay)
        await _run_due(settings, jobs=run.jobs, products=run.products)

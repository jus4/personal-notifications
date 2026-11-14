import asyncio
import logging

from sqlmodel import Session

from app.core.config import get_settings
from app.db.session import engine
from app.services.connections import manager
from app.services.jobs import JobRepository, serialize_job
from app.services.scrape import ScrapeBatchResult, run_enabled_sources
from app.services.scrape_lock import scrape_lock

logger = logging.getLogger(__name__)
settings = get_settings()


async def notify_connected_users(session: Session, job_ids: list[int]) -> None:
    repository = JobRepository(session)
    jobs = repository.list_jobs_for_notification(
        job_ids,
        posted_within_days=settings.job_recency_days,
    )
    payloads = [serialize_job(job) for job in jobs]
    notifiable_ids = [job.id for job in jobs if job.id is not None]

    for user_id in manager.user_ids():
        if not payloads:
            await manager.send_to_user(
                user_id, {"type": "no_new_jobs", "status": "success"}
            )
            continue

        sent = await manager.send_to_user(
            user_id,
            {"type": "new_jobs", "status": "success", "data": {"jobs": payloads}},
        )
        if sent:
            repository.mark_jobs_sent(user_id, notifiable_ids)
            session.commit()


async def scrape_and_notify(session: Session) -> ScrapeBatchResult:
    batch = await run_enabled_sources(session)
    await notify_connected_users(session, batch.inserted_ids)
    return batch


async def run_scrape_scheduler(interval_seconds: int) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        if scrape_lock.locked():
            logger.info("Skipping scheduled scrape: another scrape is running")
            continue

        async with scrape_lock:
            with Session(engine) as session:
                try:
                    batch = await scrape_and_notify(session)
                except Exception:
                    logger.exception("Scheduled scrape failed")
                    continue

                logger.info(
                    "Scheduled scrape finished: sources=%s failures=%s",
                    batch.source_count,
                    len(batch.failures),
                )

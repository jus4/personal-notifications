import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionFactory, get_session_factory
from app.services.connections import manager
from app.services.jobs import JobRepository, serialize_job
from app.services.scrape import ScrapeBatchResult, run_enabled_sources

logger = logging.getLogger(__name__)
settings = get_settings()

SendFn = Callable[[dict[str, Any]], Awaitable[bool]]

_notify_lock: asyncio.Lock | None = None
_notify_lock_loop: asyncio.AbstractEventLoop | None = None


def notify_lock() -> asyncio.Lock:
    """Return the notification lock for the running event loop."""
    global _notify_lock, _notify_lock_loop

    loop = asyncio.get_running_loop()
    if _notify_lock is None or _notify_lock_loop is not loop:
        _notify_lock = asyncio.Lock()
        _notify_lock_loop = loop
    return _notify_lock


async def send_unread_jobs_for_user(
    session: Session,
    user_id: int,
    *,
    send: SendFn,
    send_empty: bool,
) -> None:
    """Send a user's unread jobs through the given sender.

    The caller owns the transaction and must commit after this returns so the
    ``sent_at`` updates are persisted.
    """
    repository = JobRepository(session)
    jobs = repository.list_unread_jobs_for_user(
        user_id,
        resend_after_seconds=settings.job_reshow_after_seconds,
        posted_within_days=(
            settings.job_recency_days if repository.has_shown_history(user_id) else None
        ),
    )
    payloads = [serialize_job(job) for job in jobs]
    notifiable_ids = [job.id for job in jobs if job.id is not None]

    if not payloads:
        if send_empty:
            await send({"type": "no_new_jobs", "status": "success"})
        return

    sent = await send(
        {"type": "new_jobs", "status": "success", "data": {"jobs": payloads}}
    )
    if sent:
        repository.mark_jobs_sent(user_id, notifiable_ids)


async def _send_to_connected_user(user_id: int, payload: dict[str, Any]) -> bool:
    return (await manager.send_to_user(user_id, payload)) > 0


async def notify_connected_users(session: Session, *, send_empty: bool = True) -> None:
    """Send unread jobs to every user with a connected websocket client."""
    user_ids = manager.user_ids()
    if not user_ids:
        return

    # The scrape path and the periodic scheduler can run at the same time.
    # Serializing them keeps both from sending the same jobs twice.
    async with notify_lock():
        for user_id in user_ids:
            await send_unread_jobs_for_user(
                session,
                user_id,
                send=partial(_send_to_connected_user, user_id),
                send_empty=send_empty,
            )
        session.commit()


async def scrape_and_notify(session: Session) -> ScrapeBatchResult:
    batch = await run_enabled_sources(session)
    await notify_connected_users(session)
    return batch


async def run_notification_scheduler(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
) -> None:
    settings = settings or get_settings()
    session_factory = session_factory or get_session_factory()

    if settings.notification_interval_seconds <= 0:
        logger.info("Notification scheduler disabled")
        return

    while True:
        await asyncio.sleep(settings.notification_interval_seconds)

        if not manager.user_ids():
            # No connected clients: avoid opening a database connection.
            continue

        try:
            with session_factory() as session:
                await notify_connected_users(session, send_empty=False)
        except Exception:
            logger.exception("Periodic job notification failed")

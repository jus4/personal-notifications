import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.product_sources import router as product_sources_router
from app.api.routes.products import router as products_router
from app.api.routes.sources import router as sources_router
from app.api.routes.websocket import router as websocket_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import redis_client
from app.db.session import check_database_ready, engine
from app.services.notification_scheduler import run_notification_scheduler
from app.services.scrape_lock import scrape_lock
from app.services.scrape_scheduler import run_scrape_scheduler

logger = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting notification service (environment=%s, redis_lock=%s)",
        settings.environment,
        "distributed" if redis_client is not None else "in-process",
    )

    logger.info("Checking database connection")
    try:
        await asyncio.to_thread(check_database_ready)
    except Exception:
        logger.error("Database startup check failed; refusing to start")
        raise
    logger.info("Database connection ready")

    if redis_client is None:
        logger.info("Redis not configured; using the in-process scrape lock")
    else:
        try:
            await redis_client.ping()
            logger.info("Connected to Redis for scrape locking")
        except Exception:
            logger.warning(
                "Redis is unavailable; the scrape lock will fall back to "
                "the in-process lock",
                exc_info=True,
            )

    tasks: list[asyncio.Task[None]] = []

    if (
        settings.scrape_interval_seconds > 0
        or settings.product_scrape_interval_seconds > 0
    ):
        tasks.append(asyncio.create_task(run_scrape_scheduler(settings)))

    if settings.notification_interval_seconds > 0:
        tasks.append(asyncio.create_task(run_notification_scheduler(settings)))

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

        await scrape_lock.release()
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                logger.warning("Failed to close the Redis client", exc_info=True)
        engine.dispose()


app = FastAPI(title="Notification Service", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.is_production,
)

app.include_router(auth_router)
app.include_router(health_router)
app.include_router(dashboard_router)
app.include_router(jobs_router)
app.include_router(products_router)
app.include_router(product_sources_router)
app.include_router(sources_router)
app.include_router(websocket_router)

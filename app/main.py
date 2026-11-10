import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.websocket import router as websocket_router
from app.core.config import get_settings
from app.services.notification_scheduler import run_scrape_scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: asyncio.Task[None] | None = None
    if settings.scrape_interval_seconds > 0:
        task = asyncio.create_task(
            run_scrape_scheduler(settings.scrape_interval_seconds)
        )

    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


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
app.include_router(websocket_router)

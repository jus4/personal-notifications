from fastapi import APIRouter, HTTPException
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.redis import redis_client
from app.db.session import SessionDep

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
def health_db(session: SessionDep) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/health/redis")
async def health_redis() -> dict[str, str]:
    if redis_client is None:
        return {"status": "disabled"}
    try:
        await redis_client.ping()
    except (RedisError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok"}

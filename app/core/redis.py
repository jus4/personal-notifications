from redis.asyncio import Redis

from app.core.config import Settings, get_settings


def create_redis_client(settings: Settings) -> Redis | None:
    if not settings.redis_url:
        return None
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        health_check_interval=30,
    )


redis_client = create_redis_client(get_settings())

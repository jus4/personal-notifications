import asyncio

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services.scrape_lock import ScrapeLock

KEY = "test:scrape:lock"


def _lock(
    client: object | None,
    *,
    ttl_seconds: float = 5.0,
    renew_interval_seconds: float = 1.0,
    wait_timeout_seconds: float | None = 0.5,
) -> ScrapeLock:
    return ScrapeLock(
        key=KEY,
        ttl_seconds=ttl_seconds,
        renew_interval_seconds=renew_interval_seconds,
        wait_timeout_seconds=wait_timeout_seconds,
        client=client,  # type: ignore[arg-type]
    )


def test_acquires_and_releases_without_redis() -> None:
    async def scenario() -> None:
        lock = _lock(None)
        assert await lock.acquire() is True
        assert lock.locked()
        assert await lock.try_acquire() is False

        await lock.release()
        assert not lock.locked()

    asyncio.run(scenario())


def test_second_instance_skips_while_locked() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        first = _lock(client)
        second = _lock(client)

        assert await first.try_acquire() is True
        assert await second.try_acquire() is False

        await first.release()
        assert await second.try_acquire() is True
        await second.release()

        await client.aclose()

    asyncio.run(scenario())


def test_waiting_lock_times_out_when_held() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        first = _lock(client)
        second = _lock(client, wait_timeout_seconds=0.05)

        assert await first.acquire() is True
        assert await second.acquire(wait=True) is False
        assert second.locked() is False

        await first.release()
        await client.aclose()

    asyncio.run(scenario())


def test_release_does_not_delete_another_holders_token() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        lock = _lock(client)

        assert await lock.acquire() is True
        await client.set(KEY, "other-holder")
        await lock.release()

        assert await client.get(KEY) == "other-holder"
        await client.aclose()

    asyncio.run(scenario())


def test_renewal_extends_lock_ttl() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        lock = _lock(client, ttl_seconds=1.0, renew_interval_seconds=0.05)

        assert await lock.acquire() is True
        await asyncio.sleep(0.5)

        ttl_ms = await client.pttl(KEY)
        assert ttl_ms > 700

        await lock.release()
        await client.aclose()

    asyncio.run(scenario())


def test_lock_expires_when_not_renewed() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        first = _lock(client, ttl_seconds=0.2, renew_interval_seconds=0.05)
        second = _lock(client)

        assert await first.acquire() is True
        await first._stop_renewal()
        await asyncio.sleep(0.3)

        assert await client.exists(KEY) == 0
        assert await second.try_acquire() is True

        await second.release()
        await first.release()
        await client.aclose()

    asyncio.run(scenario())


def test_redis_failure_falls_back_to_in_process_lock() -> None:
    class BrokenRedis:
        async def set(self, *_: object, **__: object) -> bool:
            raise RedisConnectionError("redis down")

    async def scenario() -> None:
        lock = _lock(BrokenRedis())

        assert await lock.acquire() is True
        assert lock.locked()

        await lock.release()
        assert not lock.locked()

    asyncio.run(scenario())


def test_context_manager_releases_lock() -> None:
    async def scenario() -> None:
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        lock = _lock(client)

        async with lock:
            assert await client.exists(KEY) == 1

        assert await client.exists(KEY) == 0
        await client.aclose()

    asyncio.run(scenario())

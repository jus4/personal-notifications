import asyncio
import logging
import uuid
from contextlib import suppress
from typing import Self

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 0.25

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


class ScrapeLock:
    def __init__(
        self,
        *,
        key: str,
        ttl_seconds: float,
        renew_interval_seconds: float,
        wait_timeout_seconds: float | None,
        client: Redis | None,
    ) -> None:
        self._key = key
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._renew_interval = max(0.05, float(renew_interval_seconds))
        self._wait_timeout = wait_timeout_seconds
        self._redis = client
        self._local = asyncio.Lock()
        self._local_held = False
        self._redis_held = False
        self._token: str | None = None
        self._renew_task: asyncio.Task[None] | None = None

    def locked(self) -> bool:
        return self._local.locked()

    async def __aenter__(self) -> Self:
        if not await self.acquire(wait=True):
            raise TimeoutError("Timed out waiting for the scrape lock")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.release()

    async def acquire(self, *, wait: bool = True) -> bool:
        if not await self._acquire_local(wait=wait):
            return False
        try:
            redis_result = await self._acquire_redis(wait=wait)
        except BaseException:
            self._release_local()
            raise
        if redis_result is False:
            self._release_local()
            return False
        if redis_result is None:
            logger.info("Acquired in-process scrape lock %s", self._key)
        return True

    async def try_acquire(self) -> bool:
        return await self.acquire(wait=False)

    async def release(self) -> None:
        if not self._local_held and not self._redis_held:
            return

        await self._stop_renewal()

        if self._redis is not None and self._redis_held and self._token is not None:
            try:
                await self._redis.eval(
                    _RELEASE_SCRIPT,
                    1,
                    self._key,
                    self._token,
                )
            except (RedisError, OSError, TimeoutError) as exc:
                logger.warning(
                    "Failed to release Redis scrape lock %s: %s",
                    self._key,
                    exc,
                )
            finally:
                self._redis_held = False
                self._token = None
            logger.info("Released distributed scrape lock %s", self._key)
        else:
            logger.info("Released in-process scrape lock %s", self._key)

        self._release_local()

    async def _acquire_local(self, *, wait: bool) -> bool:
        if self._local_held:
            return False
        if not wait:
            if self._local.locked():
                return False
            await self._local.acquire()
            self._local_held = True
            return True

        if self._wait_timeout is None:
            await self._local.acquire()
            self._local_held = True
            return True

        if self._wait_timeout <= 0:
            if self._local.locked():
                return False
            await self._local.acquire()
            self._local_held = True
            return True

        try:
            await asyncio.wait_for(
                self._local.acquire(),
                timeout=self._wait_timeout,
            )
        except TimeoutError:
            return False
        self._local_held = True
        return True

    async def _acquire_redis(self, *, wait: bool) -> bool | None:
        if self._redis is None:
            return None

        token = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = (
            None if self._wait_timeout is None else loop.time() + self._wait_timeout
        )

        while True:
            try:
                acquired = await self._redis.set(
                    self._key,
                    token,
                    nx=True,
                    px=self._ttl_ms,
                )
            except (RedisError, OSError, TimeoutError) as exc:
                logger.warning(
                    "Redis scrape lock unavailable; falling back to the "
                    "in-process lock: %s",
                    exc,
                )
                return None

            if acquired:
                self._token = token
                self._redis_held = True
                self._start_renewal(token)
                logger.info("Acquired distributed scrape lock %s", self._key)
                return True

            if not wait:
                return False

            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                sleep_for = min(POLL_INTERVAL_SECONDS, remaining)
            else:
                sleep_for = POLL_INTERVAL_SECONDS

            await asyncio.sleep(sleep_for)

    def _start_renewal(self, token: str) -> None:
        if self._redis is None:
            return
        self._renew_task = asyncio.create_task(self._renew_loop(token))

    async def _renew_loop(self, token: str) -> None:
        assert self._redis is not None
        while True:
            await asyncio.sleep(self._renew_interval)
            try:
                renewed = await self._redis.eval(
                    _RENEW_SCRIPT,
                    1,
                    self._key,
                    token,
                    self._ttl_ms,
                )
            except (RedisError, OSError, TimeoutError) as exc:
                logger.warning(
                    "Failed to renew Redis scrape lock %s: %s",
                    self._key,
                    exc,
                )
                return
            if not renewed:
                logger.warning(
                    "Redis scrape lock %s expired before it could be renewed",
                    self._key,
                )
                return

    async def _stop_renewal(self) -> None:
        task = self._renew_task
        self._renew_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _release_local(self) -> None:
        if not self._local_held:
            return
        self._local_held = False
        if self._local.locked():
            self._local.release()


def _build_scrape_lock() -> ScrapeLock:
    settings = get_settings()
    return ScrapeLock(
        key=f"{settings.redis_key_prefix}:scrape:lock",
        ttl_seconds=settings.scrape_lock_ttl_seconds,
        renew_interval_seconds=settings.effective_scrape_lock_renew_interval,
        wait_timeout_seconds=settings.scrape_lock_wait_timeout_seconds,
        client=redis_client,
    )


scrape_lock = _build_scrape_lock()

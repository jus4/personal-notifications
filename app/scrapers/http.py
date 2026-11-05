import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_ATTEMPTS = 3
DEFAULT_HEADERS = {
    "User-Agent": "notification-service/0.1",
    "Accept": "application/json",
}


async def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> Any:
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT, headers=request_headers
    ) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt >= attempts:
                    raise
                delay = 0.5 * (2 ** (attempt - 1))
                logger.warning(
                    "Fetch failed (attempt %s/%s): %s; retrying in %ss",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    raise RuntimeError("fetch_json exhausted all attempts")

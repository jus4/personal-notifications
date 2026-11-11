import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_ATTEMPTS = 3
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}
HTML_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
}


async def _fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> httpx.Response:
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT, headers=request_headers
    ) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
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

    raise RuntimeError("fetch exhausted all attempts")


async def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> Any:
    response = await _fetch(url, headers=headers, attempts=attempts)
    try:
        return response.json()
    except ValueError:
        logger.warning("Response from %s was not valid JSON", url)
        raise


async def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> str:
    response = await _fetch(url, headers=headers, attempts=attempts)
    return response.text

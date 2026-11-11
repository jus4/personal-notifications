import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from curl_cffi import AsyncSession
from curl_cffi.requests.errors import RequestsError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
LOGGED_RESPONSE_HEADERS = (
    "server",
    "cf-ray",
    "cf-mitigated",
    "content-type",
    "location",
    "retry-after",
)
BODY_LOG_LIMIT = 1000

DEFAULT_HEADERS = {
    "Accept": "application/json",
}
HTML_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class ResponseLike(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def raise_for_status(self) -> Any: ...


def validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(
            f"Invalid URL {url!r}: expected an absolute http:// or https:// URL"
        )


def _log_failed_response(url: str, response: ResponseLike) -> None:
    headers = {
        name: response.headers[name]
        for name in LOGGED_RESPONSE_HEADERS
        if name in response.headers
    }
    try:
        body = " ".join(response.text.split())
    except (httpx.DecodingError, UnicodeDecodeError):
        body = "<unreadable response body>"

    logger.error(
        "Fetch failed with HTTP %s for %s; headers=%s; body=%r",
        response.status_code,
        url,
        headers,
        body[:BODY_LOG_LIMIT],
    )

    if response.status_code == 403 and (
        headers.get("cf-mitigated") == "challenge" or "Just a moment" in body
    ):
        logger.error(
            "Cloudflare challenge blocked %s (cf-ray=%s, cf-mitigated=%s)",
            url,
            headers.get("cf-ray"),
            headers.get("cf-mitigated"),
        )


def _retry_delay(attempt: int) -> float:
    return 0.5 * (2 ** (attempt - 1))


def _build_proxies(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def html_session(*, headers: dict[str, str] | None = None) -> AsyncSession:
    settings = get_settings()
    return AsyncSession(
        impersonate=settings.scraper_impersonate,
        timeout=DEFAULT_TIMEOUT,
        headers={**HTML_HEADERS, **(headers or {})},
        proxies=_build_proxies(settings.scraper_proxy_url),
    )


async def _fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> httpx.Response:
    validate_url(url)

    settings = get_settings()
    request_headers = {
        "User-Agent": settings.scraper_user_agent,
        **DEFAULT_HEADERS,
        **(headers or {}),
    }

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        headers=request_headers,
        proxy=settings.scraper_proxy_url or None,
    ) as client:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(url)
            except httpx.RequestError as exc:
                if attempt >= attempts:
                    raise
                delay = _retry_delay(attempt)
                logger.warning(
                    "Fetch failed (attempt %s/%s): %s; retrying in %ss",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code < 400:
                return response

            _log_failed_response(url, response)

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                delay = _retry_delay(attempt)
                logger.warning(
                    "Fetch failed (attempt %s/%s): HTTP %s; retrying in %ss",
                    attempt,
                    attempts,
                    response.status_code,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            response.raise_for_status()

    raise RuntimeError("fetch exhausted all attempts")


async def _fetch_html(
    session: AsyncSession,
    url: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
) -> ResponseLike:
    validate_url(url)

    for attempt in range(1, attempts + 1):
        try:
            response = await session.get(url)
        except RequestsError as exc:
            if attempt >= attempts:
                raise
            delay = _retry_delay(attempt)
            logger.warning(
                "Fetch failed (attempt %s/%s): %s; retrying in %ss",
                attempt,
                attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        if response.status_code < 400:
            return response

        _log_failed_response(url, response)

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
            delay = _retry_delay(attempt)
            logger.warning(
                "Fetch failed (attempt %s/%s): HTTP %s; retrying in %ss",
                attempt,
                attempts,
                response.status_code,
                delay,
            )
            await asyncio.sleep(delay)
            continue

        response.raise_for_status()

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
    session: AsyncSession | None = None,
) -> str:
    if session is not None:
        response = await _fetch_html(session, url, attempts=attempts)
        return response.text

    async with html_session(headers=headers) as client_session:
        response = await _fetch_html(client_session, url, attempts=attempts)
        return response.text

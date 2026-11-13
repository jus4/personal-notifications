import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.models.source import Source

HttpClient = Literal["curl_cffi", "httpx"]
HttpVersion = Literal["v1.1", "v2"]
DISABLED_IMPERSONATIONS = frozenset({"", "none", "off", "disabled"})


class HttpProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: HttpClient = "curl_cffi"
    impersonate: str | None = None
    user_agent: str | None = None
    proxy_env: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    http_version: HttpVersion | None = None


class SourceScraperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    http: HttpProfile = Field(default_factory=HttpProfile)


class ResolvedHttpProfile(BaseModel):
    client: HttpClient
    impersonate: str | None
    user_agent: str | None
    proxy_url: str | None
    timeout_seconds: float
    http_version: HttpVersion | None


def normalize_impersonate(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return None if cleaned.lower() in DISABLED_IMPERSONATIONS else cleaned


def _default_user_agent(settings: Settings, *, html: bool) -> str | None:
    if html:
        return settings.scraper_html_user_agent or settings.scraper_user_agent
    return settings.scraper_user_agent


def _default_proxy_url(settings: Settings, *, html: bool) -> str | None:
    if html:
        return settings.scraper_html_proxy_url or settings.scraper_proxy_url
    return settings.scraper_proxy_url


def default_http_profile(
    settings: Settings,
    *,
    html: bool,
) -> ResolvedHttpProfile:
    impersonate = normalize_impersonate(settings.scraper_impersonate) if html else None
    if not html:
        client: HttpClient = "httpx"
    else:
        client = "curl_cffi"

    if client == "curl_cffi" and impersonate is not None:
        user_agent = None
    else:
        user_agent = _default_user_agent(settings, html=html)

    return ResolvedHttpProfile(
        client=client,
        impersonate=impersonate,
        user_agent=user_agent,
        proxy_url=_default_proxy_url(settings, html=html),
        timeout_seconds=settings.scraper_timeout_seconds,
        http_version=None,
    )


def resolve_http_profile(
    source: Source | None,
    settings: Settings,
    *,
    html: bool,
    allow_default_proxy: bool = True,
) -> ResolvedHttpProfile:
    defaults = default_http_profile(settings, html=html)
    if source is None or not source.config:
        if not allow_default_proxy:
            defaults = defaults.model_copy(update={"proxy_url": None})
        return defaults

    config = SourceScraperConfig.model_validate(source.config)
    http = config.http

    client = http.client if html else "httpx"
    impersonate = normalize_impersonate(
        http.impersonate
        if http.impersonate is not None
        else settings.scraper_impersonate
    )
    if client == "httpx":
        impersonate = None

    if http.user_agent is not None:
        user_agent = http.user_agent
    elif client == "curl_cffi" and impersonate is not None:
        user_agent = None
    else:
        user_agent = _default_user_agent(settings, html=html)

    if http.proxy_env:
        proxy_url = os.getenv(http.proxy_env)
        if not proxy_url:
            raise ValueError(
                f"Proxy environment variable {http.proxy_env!r} is not set "
                f"for source {source.slug!r}"
            )
    elif allow_default_proxy:
        proxy_url = _default_proxy_url(settings, html=html)
    else:
        proxy_url = None

    return ResolvedHttpProfile(
        client=client,
        impersonate=impersonate,
        user_agent=user_agent,
        proxy_url=proxy_url,
        timeout_seconds=http.timeout_seconds or settings.scraper_timeout_seconds,
        http_version=http.http_version,
    )

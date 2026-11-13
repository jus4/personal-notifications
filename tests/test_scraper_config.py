from typing import Any

import pytest
from pydantic import ValidationError

from app.models.source import Source
from app.scrapers.config import (
    SourceScraperConfig,
    default_http_profile,
    resolve_http_profile,
)


class SettingsStub:
    scraper_user_agent = "json-agent"
    scraper_impersonate = "chrome"
    scraper_html_user_agent = "html-agent"
    scraper_proxy_url = "http://global-proxy.test:8080"
    scraper_html_proxy_url = "http://html-proxy.test:8080"
    scraper_timeout_seconds = 15.0


def _source(config: dict[str, Any] | None = None) -> Source:
    return Source(
        slug="jobly",
        kind="jobs",
        adapter="jobly_html",
        url="https://example.test/jobs",
        config=config,
    )


def test_default_html_profile_uses_html_defaults() -> None:
    profile = default_http_profile(SettingsStub(), html=True)

    assert profile.client == "curl_cffi"
    assert profile.impersonate == "chrome"
    assert profile.user_agent is None
    assert profile.proxy_url == "http://html-proxy.test:8080"
    assert profile.timeout_seconds == 15.0
    assert profile.http_version is None


def test_default_json_profile_uses_global_proxy() -> None:
    profile = default_http_profile(SettingsStub(), html=False)

    assert profile.client == "httpx"
    assert profile.impersonate is None
    assert profile.user_agent == "json-agent"
    assert profile.proxy_url == "http://global-proxy.test:8080"


def test_source_config_overrides_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBLY_PROXY_URL", "http://jobly-proxy.test:9090")
    source = _source(
        {
            "http": {
                "client": "curl_cffi",
                "impersonate": "none",
                "user_agent": "curl/8.14.1",
                "proxy_env": "JOBLY_PROXY_URL",
                "timeout_seconds": 30,
                "http_version": "v1.1",
            }
        }
    )

    profile = resolve_http_profile(source, SettingsStub(), html=True)

    assert profile.client == "curl_cffi"
    assert profile.impersonate is None
    assert profile.user_agent == "curl/8.14.1"
    assert profile.proxy_url == "http://jobly-proxy.test:9090"
    assert profile.timeout_seconds == 30
    assert profile.http_version == "v1.1"


def test_source_config_proxy_env_missing_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBLY_PROXY_URL", raising=False)
    source = _source({"http": {"proxy_env": "JOBLY_PROXY_URL"}})

    with pytest.raises(ValueError, match="JOBLY_PROXY_URL"):
        resolve_http_profile(source, SettingsStub(), html=True)


def test_httpx_html_profile_disables_impersonation() -> None:
    source = _source(
        {
            "http": {
                "client": "httpx",
                "impersonate": "chrome",
                "user_agent": "plain-agent",
                "timeout_seconds": 5,
            }
        }
    )

    profile = resolve_http_profile(source, SettingsStub(), html=True)

    assert profile.client == "httpx"
    assert profile.impersonate is None
    assert profile.user_agent == "plain-agent"
    assert profile.proxy_url == "http://html-proxy.test:8080"


def test_source_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        SourceScraperConfig.model_validate({"http": {"unknown": True}})


def test_product_profile_ignores_default_proxy() -> None:
    source = _source()

    profile = resolve_http_profile(
        source,
        SettingsStub(),
        html=True,
        allow_default_proxy=False,
    )

    assert profile.proxy_url is None


def test_product_profile_uses_explicit_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRODUCT_PROXY_URL", "http://product-proxy.test:9090")
    source = _source({"http": {"proxy_env": "PRODUCT_PROXY_URL"}})

    profile = resolve_http_profile(
        source,
        SettingsStub(),
        html=True,
        allow_default_proxy=False,
    )

    assert profile.proxy_url == "http://product-proxy.test:9090"

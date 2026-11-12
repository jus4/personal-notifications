import asyncio
import logging
from typing import Any, ClassVar, Self

import httpx
import pytest

from app.scrapers import http
from app.scrapers.config import ResolvedHttpProfile


class FakeHttpxClient:
    response_factory: Any = None
    instances: ClassVar[list["FakeHttpxClient"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls = 0
        self.__class__.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get(self, url: str) -> httpx.Response:
        self.calls += 1
        return self.__class__.response_factory(self.calls, url)


class FakeAsyncSession:
    response_factory: Any = None
    instances: ClassVar[list["FakeAsyncSession"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls = 0
        self.__class__.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get(self, url: str) -> Any:
        self.calls += 1
        return self.__class__.response_factory(self.calls, url)


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _httpx_response(url: str, status: int, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), **kwargs)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    class SettingsStub:
        scraper_user_agent = "test-agent"
        scraper_impersonate = "chrome"
        scraper_html_user_agent = None
        scraper_proxy_url = None
        scraper_html_proxy_url = None
        scraper_timeout_seconds = 15.0

    stub = SettingsStub()
    monkeypatch.setattr(http, "get_settings", lambda: stub)
    return stub


@pytest.fixture
def fake_html_session(
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> type[FakeAsyncSession]:
    FakeAsyncSession.instances.clear()
    FakeAsyncSession.response_factory = None
    monkeypatch.setattr(http, "AsyncSession", FakeAsyncSession)
    return FakeAsyncSession


@pytest.fixture
def fake_httpx(
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> type[FakeHttpxClient]:
    FakeHttpxClient.instances.clear()
    FakeHttpxClient.response_factory = None
    monkeypatch.setattr(http.httpx, "AsyncClient", FakeHttpxClient)
    return FakeHttpxClient


def test_fetch_json_sends_configured_user_agent(
    fake_httpx: type[FakeHttpxClient],
) -> None:
    url = "https://example.test/jobs"
    fake_httpx.response_factory = lambda call, requested: _httpx_response(
        requested, 200, json={"results": []}
    )

    assert asyncio.run(http.fetch_json(url)) == {"results": []}
    assert fake_httpx.instances[0].kwargs["headers"]["User-Agent"] == "test-agent"


def test_fetch_text_configures_impersonation_and_html_proxy(
    fake_html_session: type[FakeAsyncSession],
    settings: Any,
) -> None:
    settings.scraper_proxy_url = "http://global-proxy.test:8080"
    settings.scraper_html_proxy_url = "http://html-proxy.test:8080"
    settings.scraper_timeout_seconds = 30.0
    url = "https://example.test/jobs"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert asyncio.run(http.fetch_text(url)) == "ok"

    session = fake_html_session.instances[0]
    assert session.kwargs["impersonate"] == "chrome"
    assert session.kwargs["timeout"] == 30.0
    assert session.kwargs["proxies"] == {
        "http": "http://html-proxy.test:8080",
        "https": "http://html-proxy.test:8080",
    }
    assert session.kwargs["headers"]["Accept-Language"].startswith("fi-FI")
    assert "User-Agent" not in session.kwargs["headers"]


def test_fetch_text_falls_back_to_global_proxy(
    fake_html_session: type[FakeAsyncSession],
    settings: Any,
) -> None:
    settings.scraper_proxy_url = "http://global-proxy.test:8080"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert asyncio.run(http.fetch_text("https://example.test/jobs")) == "ok"

    assert fake_html_session.instances[0].kwargs["proxies"] == {
        "http": "http://global-proxy.test:8080",
        "https": "http://global-proxy.test:8080",
    }


def test_fetch_json_ignores_html_proxy(
    fake_httpx: type[FakeHttpxClient],
    settings: Any,
) -> None:
    settings.scraper_html_proxy_url = "http://html-proxy.test:8080"
    fake_httpx.response_factory = lambda call, requested: _httpx_response(
        requested, 200, json={"results": []}
    )

    assert asyncio.run(http.fetch_json("https://example.test/jobs")) == {"results": []}
    assert fake_httpx.instances[0].kwargs["proxy"] is None


def test_fetch_text_uses_provided_session(
    fake_html_session: type[FakeAsyncSession],
) -> None:
    url = "https://example.test/jobs"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )
    session = fake_html_session()

    async def run() -> None:
        assert await http.fetch_text(url, session=session) == "ok"
        assert await http.fetch_text(url, session=session) == "ok"

    asyncio.run(run())

    assert session.calls == 2
    assert fake_html_session.instances == [session]


def test_fetch_text_rejects_url_without_scheme(
    fake_html_session: type[FakeAsyncSession],
) -> None:
    with pytest.raises(ValueError, match="Invalid URL"):
        asyncio.run(http.fetch_text("example.test/jobs"))


def test_fetch_text_logs_403_without_retrying(
    fake_html_session: type[FakeAsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "https://example.test/jobs"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        403,
        text="<html><title>Just a moment...</title>Sorry, you have been blocked</html>",
        headers={
            "server": "cloudflare",
            "cf-ray": "abc123",
            "cf-mitigated": "challenge",
        },
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        asyncio.run(http.fetch_text(url))

    assert fake_html_session.instances[0].calls == 1
    assert "HTTP 403" in caplog.text
    assert "cf-ray" in caplog.text
    assert "abc123" in caplog.text
    assert "Just a moment" in caplog.text
    assert "Cloudflare challenge blocked" in caplog.text


def test_fetch_text_retries_retryable_status(
    fake_html_session: type[FakeAsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/jobs"

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)

    def response_factory(call: int, requested: str) -> FakeResponse:
        if call == 1:
            return FakeResponse(503, text="unavailable")
        return FakeResponse(200, text="ok")

    fake_html_session.response_factory = response_factory

    assert asyncio.run(http.fetch_text(url)) == "ok"
    assert fake_html_session.instances[0].calls == 2


def test_fetch_text_without_proxies(
    fake_html_session: type[FakeAsyncSession],
) -> None:
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert asyncio.run(http.fetch_text("https://example.test/jobs")) == "ok"
    assert fake_html_session.instances[0].kwargs["proxies"] is None


def test_fetch_text_disables_impersonation_with_minimal_headers(
    fake_html_session: type[FakeAsyncSession],
    settings: Any,
) -> None:
    settings.scraper_impersonate = ""
    settings.scraper_html_user_agent = "curl/8.5.0"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert (
        asyncio.run(
            http.fetch_text(
                "https://example.test/jobs",
                headers={"Referer": "https://www.jobly.fi/"},
            )
        )
        == "ok"
    )

    session = fake_html_session.instances[0]
    assert session.kwargs["impersonate"] is None
    assert session.kwargs["headers"] == {
        "Accept": "*/*",
        "User-Agent": "curl/8.5.0",
    }


def test_fetch_text_plain_mode_falls_back_to_scraper_user_agent(
    fake_html_session: type[FakeAsyncSession],
    settings: Any,
) -> None:
    settings.scraper_impersonate = "none"
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert asyncio.run(http.fetch_text("https://example.test/jobs")) == "ok"

    session = fake_html_session.instances[0]
    assert session.kwargs["impersonate"] is None
    assert session.kwargs["headers"]["User-Agent"] == "test-agent"


def test_fetch_text_uses_explicit_profile(
    fake_html_session: type[FakeAsyncSession],
) -> None:
    profile = ResolvedHttpProfile(
        client="curl_cffi",
        impersonate=None,
        user_agent="curl/9.0.0",
        proxy_url="http://profile-proxy.test:8080",
        timeout_seconds=42.0,
        http_version="v1.1",
    )
    fake_html_session.response_factory = lambda call, requested: FakeResponse(
        200, text="ok"
    )

    assert (
        asyncio.run(http.fetch_text("https://example.test/jobs", profile=profile))
        == "ok"
    )

    session = fake_html_session.instances[0]
    assert session.kwargs["impersonate"] is None
    assert session.kwargs["timeout"] == 42.0
    assert session.kwargs["http_version"] == "v1.1"
    assert session.kwargs["proxies"] == {
        "http": "http://profile-proxy.test:8080",
        "https": "http://profile-proxy.test:8080",
    }
    assert session.kwargs["headers"] == {
        "Accept": "*/*",
        "User-Agent": "curl/9.0.0",
    }

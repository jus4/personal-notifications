import asyncio
import logging
from typing import Any, ClassVar, Self

import httpx
import pytest

from app.scrapers import http


class FakeAsyncClient:
    response_factory: Any = None
    instances: ClassVar[list["FakeAsyncClient"]] = []

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


def _response(url: str, status: int, **kwargs: Any) -> httpx.Response:
    return httpx.Response(
        status,
        request=httpx.Request("GET", url),
        **kwargs,
    )


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeAsyncClient]:
    FakeAsyncClient.instances.clear()
    FakeAsyncClient.response_factory = None
    monkeypatch.setattr(http.httpx, "AsyncClient", FakeAsyncClient)

    class SettingsStub:
        scraper_user_agent = "test-agent"
        scraper_proxy_url = None

    monkeypatch.setattr(http, "get_settings", lambda: SettingsStub())
    return FakeAsyncClient


def test_fetch_text_rejects_url_without_scheme(
    fake_client: type[FakeAsyncClient],
) -> None:
    with pytest.raises(ValueError, match="Invalid URL"):
        asyncio.run(http.fetch_text("example.test/jobs"))


def test_fetch_text_sends_configured_user_agent(
    fake_client: type[FakeAsyncClient],
) -> None:
    url = "https://example.test/jobs"
    fake_client.response_factory = lambda call, requested: _response(
        requested, 200, text="ok"
    )

    assert asyncio.run(http.fetch_text(url)) == "ok"
    assert fake_client.instances[0].kwargs["headers"]["User-Agent"] == "test-agent"


def test_fetch_text_logs_403_without_retrying(
    fake_client: type[FakeAsyncClient],
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = "https://example.test/jobs"
    fake_client.response_factory = lambda call, requested: _response(
        requested,
        403,
        text="<html>Sorry, you have been blocked</html>",
        headers={
            "server": "cloudflare",
            "cf-ray": "abc123",
            "cf-mitigated": "challenge",
        },
    )

    with caplog.at_level(logging.ERROR), pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http.fetch_text(url))

    assert fake_client.instances[0].calls == 1
    assert "HTTP 403" in caplog.text
    assert "cf-ray" in caplog.text
    assert "abc123" in caplog.text
    assert "Sorry, you have been blocked" in caplog.text


def test_fetch_text_retries_retryable_status(
    fake_client: type[FakeAsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.test/jobs"

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)

    def response_factory(call: int, requested: str) -> httpx.Response:
        if call == 1:
            return _response(requested, 503, text="unavailable")
        return _response(requested, 200, text="ok")

    fake_client.response_factory = response_factory

    assert asyncio.run(http.fetch_text(url)) == "ok"
    assert fake_client.instances[0].calls == 2

import asyncio
from typing import Any

import pytest
from sqlmodel import Session

from app.models.source import Source
from app.scrapers.base import ScrapedJob
from app.scrapers.registry import JOB_ADAPTERS
from app.services.scrape import run_source


def _make_source(adapter: str = "fake") -> Source:
    return Source(
        slug="fake-source",
        kind="jobs",
        adapter=adapter,
        url="https://example.test/jobs",
    )


def test_run_source_uses_registered_adapter(
    monkeypatch: Any,
    session: Session,
) -> None:
    class FakeAdapter:
        key = "fake"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            assert source.slug == "fake-source"
            return [ScrapedJob(external_id="1", title="Fake job")]

    monkeypatch.setitem(JOB_ADAPTERS, FakeAdapter.key, FakeAdapter)

    result = asyncio.run(run_source(session, _make_source()))

    assert result.source == "fake-source"
    assert result.fetched == 1
    assert result.inserted == 1
    assert result.updated == 0


def test_run_source_rejects_unknown_adapter(session: Session) -> None:
    with pytest.raises(ValueError, match="No job adapter registered"):
        asyncio.run(run_source(session, _make_source(adapter="missing")))

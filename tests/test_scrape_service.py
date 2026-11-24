import asyncio
import logging
from typing import Any

import pytest
from sqlmodel import Session

from app.models.source import Source
from app.scrapers.base import ScrapedJob
from app.scrapers.registry import JOB_ADAPTERS
from app.services.scrape import run_enabled_sources, run_source


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


def test_run_source_returns_inserted_ids_only_for_new_jobs(
    monkeypatch: Any,
    session: Session,
) -> None:
    class FakeAdapter:
        key = "fake-inserted"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            return [
                ScrapedJob(external_id="1", title="Fake job"),
                ScrapedJob(external_id="2", title="Another job"),
            ]

    monkeypatch.setitem(JOB_ADAPTERS, FakeAdapter.key, FakeAdapter)
    source = _make_source(adapter="fake-inserted")

    first = asyncio.run(run_source(session, source))
    assert len(first.inserted_ids) == 2

    second = asyncio.run(run_source(session, source))
    assert second.inserted_ids == []
    assert second.updated == 2


def test_run_source_rejects_unknown_adapter(session: Session) -> None:
    with pytest.raises(ValueError, match="No job adapter registered"):
        asyncio.run(run_source(session, _make_source(adapter="missing")))


def test_run_enabled_sources_logs_lifecycle(
    monkeypatch: Any,
    session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeAdapter:
        key = "fake-log"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            return [ScrapedJob(external_id="1", title="Fake job")]

    monkeypatch.setitem(JOB_ADAPTERS, FakeAdapter.key, FakeAdapter)
    session.add(_make_source(adapter="fake-log"))
    session.commit()

    with caplog.at_level(logging.INFO, logger="app.services.scrape"):
        asyncio.run(run_enabled_sources(session))

    assert "Starting job scrape: sources=1" in caplog.text
    assert "Scraped jobs fake-source" in caplog.text
    assert "Job scrape finished: sources=1 inserted=1 failures=0" in caplog.text

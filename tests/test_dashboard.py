from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.job import Job
from app.models.source import Source
from app.scrapers.base import ScrapedJob
from app.scrapers.registry import JOB_ADAPTERS


def test_dashboard_requires_authentication(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dashboard_shows_job_and_source_stats(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    scraped_at = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    session.add(
        Source(
            slug="duunitori",
            kind="jobs",
            adapter="duunitori_json",
            url="https://example.test/jobs",
            enabled=True,
        )
    )
    session.add(
        Source(
            slug="disabled-source",
            kind="jobs",
            adapter="duunitori_json",
            enabled=False,
        )
    )
    session.add(
        Job(
            external_id="1",
            source="duunitori",
            title="Backend Developer",
            scraped_at=scraped_at,
            raw_data={"link": "https://example.test/jobs/1"},
        )
    )
    session.commit()

    response = authenticated_client.get("/")

    assert response.status_code == 200
    assert "Total jobs" in response.text
    assert "duunitori" in response.text
    assert "disabled-source" in response.text
    assert "Enabled" in response.text
    assert "Disabled" in response.text
    assert "2026-11-05 14:30 EET" in response.text
    assert "Scrape jobs" in response.text
    assert 'href="/jobs"' in response.text


def test_dashboard_ignores_product_sources(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    session.add(
        Source(
            slug="bergfreunde",
            kind="products",
            adapter="bergfreunde_html",
            enabled=True,
        )
    )
    session.commit()

    response = authenticated_client.get("/")

    assert response.status_code == 200
    assert "bergfreunde" not in response.text


def test_scrape_requires_authentication(client: TestClient) -> None:
    response = client.post("/scrape", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_scrape_runs_enabled_sources_and_shows_results(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        key = "fake"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            assert source.slug == "fake-source"
            return [
                ScrapedJob(
                    external_id="1",
                    title="Fake job",
                    raw_data={"link": "https://example.test/fake/1"},
                )
            ]

    monkeypatch.setitem(JOB_ADAPTERS, FakeAdapter.key, FakeAdapter)
    session.add(
        Source(
            slug="fake-source",
            kind="jobs",
            adapter="fake",
            url="https://example.test/jobs",
            enabled=True,
        )
    )
    session.commit()

    response = authenticated_client.post("/scrape")

    assert response.status_code == 200
    assert "Scrape results" in response.text
    assert "fake-source" in response.text
    assert "Inserted" in response.text

    jobs = session.exec(select(Job).where(Job.source == "fake-source")).all()
    assert [job.title for job in jobs] == ["Fake job"]


def test_scrape_htmx_refreshes_dashboard_stats(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        key = "fake"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            return [ScrapedJob(external_id="1", title="Fake job")]

    monkeypatch.setitem(JOB_ADAPTERS, FakeAdapter.key, FakeAdapter)
    monkeypatch.setattr(
        "app.services.jobs.utcnow",
        lambda: datetime(2026, 11, 6, 12, 30, tzinfo=UTC),
    )
    session.add(
        Source(
            slug="fake-source",
            kind="jobs",
            adapter="fake",
            url="https://example.test/jobs",
            enabled=True,
        )
    )
    session.add(
        Job(
            external_id="old",
            source="fake-source",
            title="Old job",
            scraped_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        )
    )
    session.commit()

    response = authenticated_client.post(
        "/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Scrape results" in response.text
    assert 'id="dashboard-stats"' in response.text
    assert "2026-11-06 14:30 EET" in response.text
    assert "2026-01-01 12:00 EET" not in response.text
    assert 'id="app-header"' not in response.text


def test_scrape_continues_after_source_failure(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadAdapter:
        key = "bad"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            raise RuntimeError("boom")

    class GoodAdapter:
        key = "good"

        async def fetch(self, source: Source) -> list[ScrapedJob]:
            return [ScrapedJob(external_id="1", title="Good job")]

    monkeypatch.setitem(JOB_ADAPTERS, BadAdapter.key, BadAdapter)
    monkeypatch.setitem(JOB_ADAPTERS, GoodAdapter.key, GoodAdapter)
    session.add(
        Source(
            slug="bad-source",
            kind="jobs",
            adapter="bad",
            url="https://example.test/bad",
            enabled=True,
        )
    )
    session.add(
        Source(
            slug="good-source",
            kind="jobs",
            adapter="good",
            url="https://example.test/good",
            enabled=True,
        )
    )
    session.commit()

    response = authenticated_client.post("/scrape")

    assert response.status_code == 200
    assert "bad-source" in response.text
    assert "boom" in response.text
    assert "good-source" in response.text

    jobs = session.exec(select(Job).where(Job.source == "good-source")).all()
    assert [job.title for job in jobs] == ["Good job"]


def test_scrape_without_enabled_sources_shows_message(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post("/scrape")

    assert response.status_code == 200
    assert "No enabled jobs sources to scrape." in response.text

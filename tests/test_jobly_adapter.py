import asyncio
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.jobs import jobly

SOURCE_URL = (
    "https://www.jobly.fi/tyopaikat/it?search=&job_geo_location=Uusimaa%2C%20Suomi"
    "&administrative_area_level_1=Uusimaa&page=0"
)


@pytest.fixture(autouse=True)
def _no_page_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobly, "PAGE_DELAY_SECONDS", 0)


def _make_source(url: str = SOURCE_URL) -> Source:
    return Source(
        slug="jobly",
        kind="jobs",
        adapter="jobly_html",
        url=url,
    )


def _article(
    *,
    href: str,
    title: str,
    date: str = "11.11.2026,              ",
    location: str = "Helsinki",
) -> str:
    return f"""
    <article class="node node-job node-teaser">
      <h2 class="node__title node__title">
        <a href="{href}" class="recruiter-job-link" title="{title}">{title}</a>
      </h2>
      <div class="description">
        <span class="date">{date}</span>
        <span class="recruiter-company-profile-job-organization">Acme Oy</span>
      </div>
      <div class="location"><span>{location}</span></div>
    </article>
    """


def test_fetch_maps_jobly_jobs(monkeypatch: Any) -> None:
    html = _article(
        href="https://www.jobly.fi/tyopaikka/senior-data-engineer-2783297",
        title="Senior Data Engineer",
    ) + _article(
        href="/tyopaikka/integraatioarkkitehti-lahitapiola-2787385",
        title="Integraatioarkkitehti",
        date="09.11.2026,",
        location="Espoo",
    )
    requested: list[str] = []

    async def fake_fetch_text(url: str, **kwargs: Any) -> str:
        requested.append(url)
        return html if len(requested) == 1 else ""

    monkeypatch.setattr(jobly, "fetch_text", fake_fetch_text)

    jobs = asyncio.run(jobly.JoblyJobAdapter().fetch(_make_source()))

    assert [job.external_id for job in jobs] == ["2783297", "2787385"]
    assert [job.title for job in jobs] == [
        "Senior Data Engineer",
        "Integraatioarkkitehti",
    ]
    assert jobs[0].raw_data == {
        "link": "https://www.jobly.fi/tyopaikka/senior-data-engineer-2783297",
        "date_posted": "2026-11-11",
        "location": "Helsinki",
    }
    assert jobs[1].raw_data == {
        "link": "https://www.jobly.fi/tyopaikka/integraatioarkkitehti-lahitapiola-2787385",
        "date_posted": "2026-11-09",
        "location": "Espoo",
    }
    assert "page=0" in requested[0]
    assert "job_geo_location=Uusimaa%2C%20Suomi" in requested[0]
    assert len(requested) == 2


def test_fetch_pages_up_to_max_pages(monkeypatch: Any) -> None:
    requested: list[str] = []

    async def fake_fetch_text(url: str, **kwargs: Any) -> str:
        requested.append(url)
        page = len(requested) - 1
        return _article(
            href=f"/tyopaikka/job-{1000 + page}",
            title=f"Job {page}",
        )

    monkeypatch.setattr(jobly, "fetch_text", fake_fetch_text)

    jobs = asyncio.run(jobly.JoblyJobAdapter().fetch(_make_source()))

    assert len(requested) == jobly.MAX_PAGES
    assert [job.external_id for job in jobs] == [
        str(1000 + page) for page in range(jobly.MAX_PAGES)
    ]
    assert [f"page={page}" in url for page, url in enumerate(requested)] == [
        True
    ] * jobly.MAX_PAGES


def test_fetch_skips_invalid_and_duplicate_entries(monkeypatch: Any) -> None:
    html = (
        _article(href="/tyopaikka/first-1", title="First")
        + _article(href="/tyopaikka/first-1", title="Duplicate")
        + """
        <article class="node-job">
          <h2 class="node__title"><a href="/tyopaikka/no-id">Missing id</a></h2>
        </article>
        """
        + """
        <article class="node-job">
          <h2 class="node__title"><a href="/tyopaikka/empty-title-2"> </a></h2>
        </article>
        """
    )

    async def fake_fetch_text(url: str, **kwargs: Any) -> str:
        return html

    monkeypatch.setattr(jobly, "fetch_text", fake_fetch_text)

    jobs = asyncio.run(jobly.JoblyJobAdapter().fetch(_make_source()))

    assert [job.external_id for job in jobs] == ["1"]
    assert jobs[0].title == "First"


def test_fetch_requires_a_source_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(jobly.JoblyJobAdapter().fetch(_make_source(url="")))

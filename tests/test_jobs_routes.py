from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.job import Job


def _add_job(
    session: Session,
    *,
    external_id: str,
    title: str,
    source: str = "duunitori",
    posted_at: str | None = "2026-11-04T12:00:00+02:00",
    date_created: str | None = None,
    link: str = "https://example.test/jobs/1",
    scraped_at: datetime | None = None,
) -> None:
    raw_data: dict[str, str] = {"link": link}
    if posted_at is not None:
        raw_data["date_posted"] = posted_at
    if date_created is not None:
        raw_data["date_created"] = date_created

    session.add(
        Job(
            external_id=external_id,
            source=source,
            title=title,
            scraped_at=scraped_at or datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
            raw_data=raw_data,
        )
    )


def test_jobs_page_requires_authentication(client: TestClient) -> None:
    response = client.get("/jobs", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_jobs_page_shows_basic_job_information(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="Backend Developer",
        link="https://example.test/jobs/backend",
        posted_at="2026-11-04T12:00:00+02:00",
        scraped_at=datetime(2026, 11, 5, 9, 15, tzinfo=UTC),
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    assert response.status_code == 200
    assert "Backend Developer" in response.text
    assert "https://example.test/jobs/backend" in response.text
    assert "2026-11-04" in response.text
    assert "2026-11-05" in response.text
    assert "duunitori" in response.text
    assert "Apply filters" in response.text
    assert "data-local-time" in response.text
    assert 'data-date-only="true"' in response.text
    assert 'datetime="2026-11-05T09:15:00+00:00"' in response.text


def test_jobs_page_converts_dates_to_helsinki(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="Late job",
        scraped_at=datetime(2026, 11, 5, 23, 30, tzinfo=UTC),
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    assert response.status_code == 200
    assert "2026-11-06" in response.text
    assert 'datetime="2026-11-05T23:30:00+00:00"' in response.text


def test_jobs_page_orders_by_date_posted(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    scraped_at = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    _add_job(
        session,
        external_id="1",
        title="Older posted",
        posted_at="2026-11-01T12:00:00+02:00",
        scraped_at=scraped_at,
    )
    _add_job(
        session,
        external_id="2",
        title="Newer posted",
        posted_at="2026-11-05T12:00:00+02:00",
        scraped_at=scraped_at,
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    assert response.text.index("Newer posted") < response.text.index("Older posted")


def test_jobs_page_orders_offsets_chronologically(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="Offset earlier instant",
        posted_at="2026-11-04T23:30:00+02:00",
    )
    _add_job(
        session,
        external_id="2",
        title="Offset later instant",
        posted_at="2026-11-04T22:00:00+00:00",
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    assert response.text.index("Offset later instant") < response.text.index(
        "Offset earlier instant"
    )


def test_jobs_page_orders_date_created_fallback_and_puts_nulls_last(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="No posted date",
        posted_at=None,
    )
    _add_job(
        session,
        external_id="2",
        title="Fallback created date",
        posted_at=None,
        date_created="2026-11-05T12:00:00+00:00",
    )
    _add_job(
        session,
        external_id="3",
        title="Posted date",
        posted_at="2026-11-04T12:00:00+02:00",
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    fallback_index = response.text.index("Fallback created date")
    posted_index = response.text.index("Posted date")
    missing_index = response.text.index("No posted date")
    assert fallback_index < posted_index < missing_index


def test_jobs_page_does_not_render_unsafe_links(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="Unsafe job",
        link="javascript:alert(1)",
    )
    session.commit()

    response = authenticated_client.get("/jobs")

    assert response.status_code == 200
    assert "javascript:alert(1)" not in response.text


def test_jobs_page_filters_by_title(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(session, external_id="1", title="Backend Developer")
    _add_job(
        session,
        external_id="2",
        title="Frontend Developer",
        source="other",
    )
    session.commit()

    response = authenticated_client.get("/jobs", params={"q": "frontend"})

    assert "Frontend Developer" in response.text
    assert "Backend Developer" not in response.text


def test_jobs_page_filters_by_source(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(session, external_id="1", title="Backend Developer")
    _add_job(
        session,
        external_id="2",
        title="Frontend Developer",
        source="other",
    )
    session.commit()

    response = authenticated_client.get("/jobs", params={"source": "duunitori"})

    assert "Backend Developer" in response.text
    assert "Frontend Developer" not in response.text


def test_jobs_page_filters_by_posted_date_range(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    _add_job(
        session,
        external_id="1",
        title="Older job",
        posted_at="2026-11-01T12:00:00+02:00",
    )
    _add_job(
        session,
        external_id="2",
        title="Newer job",
        posted_at="2026-11-05T12:00:00+02:00",
    )
    session.commit()

    response = authenticated_client.get(
        "/jobs",
        params={"posted_from": "2026-11-03"},
    )

    assert "Newer job" in response.text
    assert "Older job" not in response.text

    response = authenticated_client.get(
        "/jobs",
        params={"posted_to": "2026-11-03"},
    )

    assert "Older job" in response.text
    assert "Newer job" not in response.text


def test_jobs_page_paginates_results(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    for index in range(30):
        _add_job(
            session,
            external_id=str(index),
            title=f"Job {index:02d}",
            link=f"https://example.test/jobs/{index}",
        )
    session.commit()

    response = authenticated_client.get("/jobs", params={"page": 2})

    assert response.status_code == 200
    assert "30 jobs" in response.text
    assert "Page 2 of 2" in response.text
    assert "Job 01" in response.text
    assert "Job 30" not in response.text


def test_jobs_page_htmx_returns_table_partial(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get(
        "/jobs",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Apply filters" not in response.text
    assert 'id="app-header"' not in response.text
    assert "No jobs match the current filters." in response.text

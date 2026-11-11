from sqlmodel import Session, select

from app.models.job import Job
from app.scrapers.base import ScrapedJob
from app.services.jobs import JobRepository


def test_upsert_inserts_new_jobs(session: Session) -> None:
    result = JobRepository(session).upsert_many(
        "duunitori",
        [
            ScrapedJob(external_id="1", title="First"),
            ScrapedJob(external_id="2", title="Second", raw_data={"id": 2}),
        ],
    )

    assert result.fetched == 2
    assert result.inserted == 2
    assert result.updated == 0
    assert result.skipped == 0

    jobs = session.exec(select(Job).where(Job.source == "duunitori")).all()
    assert {job.external_id for job in jobs} == {"1", "2"}


def test_upsert_reports_inserted_ids_only(session: Session) -> None:
    repository = JobRepository(session)

    first = repository.upsert_many(
        "duunitori",
        [ScrapedJob(external_id="1", title="First")],
    )

    first_job = session.exec(select(Job).where(Job.external_id == "1")).one()
    assert first.inserted_ids == [first_job.id]

    second = repository.upsert_many(
        "duunitori",
        [
            ScrapedJob(external_id="1", title="Updated"),
            ScrapedJob(external_id="2", title="Second"),
        ],
    )

    second_job = session.exec(select(Job).where(Job.external_id == "2")).one()
    assert second.inserted == 1
    assert second.updated == 1
    assert second.inserted_ids == [second_job.id]


def test_upsert_updates_existing_and_skips_duplicates(session: Session) -> None:
    repository = JobRepository(session)
    repository.upsert_many(
        "duunitori",
        [ScrapedJob(external_id="1", title="Old", raw_data={"version": 1})],
    )

    existing = session.exec(select(Job).where(Job.source == "duunitori")).one()
    original_id = existing.id
    original_scraped_at = existing.scraped_at

    result = repository.upsert_many(
        "duunitori",
        [
            ScrapedJob(external_id="1", title="New", raw_data={"version": 2}),
            ScrapedJob(external_id="1", title="Newest", raw_data={"version": 3}),
        ],
    )

    assert result.fetched == 2
    assert result.inserted == 0
    assert result.updated == 1
    assert result.skipped == 1

    updated = session.exec(select(Job).where(Job.source == "duunitori")).one()
    assert updated.id == original_id
    assert updated.title == "Newest"
    assert updated.raw_data == {"version": 3}
    assert updated.scraped_at >= original_scraped_at

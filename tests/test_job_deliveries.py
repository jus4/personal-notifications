from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.models.job import Job
from app.models.user import User
from app.services.jobs import JobRepository


def _add_jobs(session: Session, count: int) -> list[Job]:
    session.add(User(id=1, name="admin"))
    jobs = [
        Job(
            external_id=str(index),
            source="test",
            title=f"Job {index}",
            raw_data={"link": f"https://example.test/{index}"},
        )
        for index in range(count)
    ]
    session.add_all(jobs)
    session.commit()
    return jobs


def test_unread_jobs_excludes_shown_jobs(session: Session) -> None:
    jobs = _add_jobs(session, 2)
    repository = JobRepository(session)
    job_id = jobs[0].id
    assert job_id is not None

    repository.mark_jobs_shown(1, [job_id])
    session.commit()

    unread = repository.list_unread_jobs_for_user(1, resend_after_seconds=0)

    assert [job.id for job in unread] == [jobs[1].id]


def test_sent_jobs_respect_resend_window(session: Session) -> None:
    jobs = _add_jobs(session, 2)
    repository = JobRepository(session)
    job_id = jobs[0].id
    assert job_id is not None

    repository.mark_jobs_sent(1, [job_id])
    session.commit()

    within_window = repository.list_unread_jobs_for_user(1, resend_after_seconds=3600)
    assert [job.id for job in within_window] == [jobs[1].id]

    after_window = repository.list_unread_jobs_for_user(1, resend_after_seconds=0)
    assert {job.id for job in after_window} == {jobs[0].id, jobs[1].id}


def test_unread_jobs_respects_limit(session: Session) -> None:
    _add_jobs(session, 15)
    repository = JobRepository(session)

    unread = repository.list_unread_jobs_for_user(1, limit=10)

    assert len(unread) == 10


def test_unread_jobs_respects_recency_window(session: Session) -> None:
    now = datetime.now(UTC)
    session.add(User(id=1, name="admin"))
    session.add_all(
        [
            Job(
                external_id="recent",
                source="test",
                title="Recent",
                raw_data={"date_posted": now.isoformat()},
            ),
            Job(
                external_id="old",
                source="test",
                title="Old",
                raw_data={"date_posted": (now - timedelta(days=40)).isoformat()},
            ),
            Job(external_id="undated", source="test", title="Undated"),
        ]
    )
    session.commit()

    unread = JobRepository(session).list_unread_jobs_for_user(1, posted_within_days=30)

    assert {job.external_id for job in unread} == {"recent"}


def test_has_shown_history(session: Session) -> None:
    jobs = _add_jobs(session, 1)
    repository = JobRepository(session)
    job_id = jobs[0].id
    assert job_id is not None

    assert repository.has_shown_history(1) is False

    repository.mark_jobs_sent(1, [job_id])
    session.commit()
    assert repository.has_shown_history(1) is False

    repository.mark_jobs_shown(1, [job_id])
    session.commit()
    assert repository.has_shown_history(1) is True

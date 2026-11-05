from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.common import utcnow
from app.models.job import Job
from app.scrapers.base import ScrapedJob


@dataclass(slots=True)
class UpsertResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_many(self, source: str, jobs: list[ScrapedJob]) -> UpsertResult:
        result = UpsertResult(fetched=len(jobs))

        unique_jobs: dict[str, ScrapedJob] = {}
        for job in jobs:
            unique_jobs[job.external_id] = job
        result.skipped = len(jobs) - len(unique_jobs)

        if not unique_jobs:
            return result

        existing = {
            job.external_id: job
            for job in self.session.exec(
                select(Job).where(
                    Job.source == source,
                    Job.external_id.in_(list(unique_jobs)),
                )
            ).all()
        }

        scraped_at = utcnow()
        for external_id, scraped_job in unique_jobs.items():
            current = existing.get(external_id)
            if current is None:
                self.session.add(
                    Job(
                        external_id=external_id,
                        source=source,
                        title=scraped_job.title,
                        raw_data=scraped_job.raw_data,
                        scraped_at=scraped_at,
                    )
                )
                result.inserted += 1
                continue

            current.title = scraped_job.title
            current.raw_data = scraped_job.raw_data
            current.scraped_at = scraped_at
            self.session.add(current)
            result.updated += 1

        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return result

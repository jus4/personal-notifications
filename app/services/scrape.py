from dataclasses import dataclass

from sqlmodel import Session

from app.models.source import Source
from app.scrapers.base import JobSourceAdapter
from app.scrapers.registry import JOB_ADAPTERS
from app.services.jobs import JobRepository


@dataclass(slots=True)
class ScrapeResult:
    source: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


async def run_source(session: Session, source: Source) -> ScrapeResult:
    adapter_cls = JOB_ADAPTERS.get(source.adapter)
    if adapter_cls is None:
        raise ValueError(
            f"No job adapter registered for {source.adapter!r} (source {source.slug!r})"
        )

    adapter: JobSourceAdapter = adapter_cls()
    jobs = await adapter.fetch(source)
    upsert = JobRepository(session).upsert_many(source.slug, jobs)

    return ScrapeResult(
        source=source.slug,
        fetched=upsert.fetched,
        inserted=upsert.inserted,
        updated=upsert.updated,
        skipped=upsert.skipped,
        failed=upsert.failed,
    )

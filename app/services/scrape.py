import logging
from dataclasses import dataclass, field

from sqlmodel import Session, col, select

from app.models.source import Source
from app.scrapers.base import JobSourceAdapter
from app.scrapers.registry import JOB_ADAPTERS
from app.services.jobs import JobRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScrapeResult:
    source: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    inserted_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class ScrapeFailure:
    source: str
    error: str


@dataclass(slots=True)
class ScrapeBatchResult:
    results: list[ScrapeResult] = field(default_factory=list)
    failures: list[ScrapeFailure] = field(default_factory=list)
    source_count: int = 0
    inserted_ids: list[int] = field(default_factory=list)


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
        inserted_ids=upsert.inserted_ids,
    )


async def run_enabled_sources(
    session: Session,
    kind: str = "jobs",
) -> ScrapeBatchResult:
    sources = list(
        session.exec(
            select(Source)
            .where(col(Source.enabled).is_(True), col(Source.kind) == kind)
            .order_by(Source.slug)
        ).all()
    )

    batch = ScrapeBatchResult(source_count=len(sources))
    for source in sources:
        slug = source.slug
        try:
            result = await run_source(session, source)
            batch.results.append(result)
            batch.inserted_ids.extend(result.inserted_ids)
        except Exception as exc:
            session.rollback()
            logger.exception("Scrape failed for %s", slug)
            batch.failures.append(ScrapeFailure(source=slug, error=str(exc)))

    return batch

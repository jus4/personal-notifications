from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import DateTime, cast, desc, func
from sqlmodel import Session, select

from app.models.common import utcnow
from app.models.job import Job
from app.models.source import Source
from app.scrapers.base import ScrapedJob


@dataclass(slots=True)
class UpsertResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(slots=True)
class JobFilters:
    q: str | None = None
    source: str | None = None
    posted_from: date | None = None
    posted_to: date | None = None


@dataclass(slots=True)
class JobPage:
    jobs: list[Job]
    total: int
    page: int
    pages: int
    per_page: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def previous_page(self) -> int | None:
        return self.page - 1 if self.has_previous else None

    @property
    def next_page(self) -> int | None:
        return self.page + 1 if self.has_next else None


@dataclass(slots=True)
class SourceJobStats:
    source: str
    enabled: bool
    jobs: int
    last_scraped_at: datetime | None


@dataclass(slots=True)
class DashboardStats:
    total_jobs: int
    total_sources: int
    enabled_sources: int
    last_scraped_at: datetime | None
    sources: list[SourceJobStats]


class JobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _dialect_name(self) -> str:
        bind = self.session.get_bind()
        return bind.dialect.name if bind is not None else ""

    def _posted_date_expr(self):
        if self._dialect_name() in {"postgres", "postgresql"}:
            return func.coalesce(
                Job.raw_data.op("->>")("date_posted"),
                Job.raw_data.op("->>")("date_created"),
            )
        return func.coalesce(
            func.json_extract(Job.raw_data, "$.date_posted"),
            func.json_extract(Job.raw_data, "$.date_created"),
        )

    def _posted_date_sort_expr(self):
        posted = self._posted_date_expr()
        if self._dialect_name() in {"postgres", "postgresql"}:
            return cast(posted, DateTime(timezone=True))
        return func.julianday(posted)

    def list_jobs(
        self,
        filters: JobFilters,
        *,
        page: int = 1,
        per_page: int = 25,
    ) -> JobPage:
        conditions = []
        if filters.q:
            conditions.append(Job.title.ilike(f"%{filters.q}%"))
        if filters.source:
            conditions.append(Job.source == filters.source)
        if filters.posted_from or filters.posted_to:
            posted_date = func.substr(self._posted_date_expr(), 1, 10)
            if filters.posted_from:
                conditions.append(posted_date >= filters.posted_from.isoformat())
            if filters.posted_to:
                conditions.append(posted_date <= filters.posted_to.isoformat())

        count_statement = select(func.count(Job.id))
        for condition in conditions:
            count_statement = count_statement.where(condition)
        total = self.session.exec(count_statement).one()

        pages = (total + per_page - 1) // per_page if total else 0
        page = min(page, pages) if pages else 1

        statement = select(Job)
        for condition in conditions:
            statement = statement.where(condition)
        statement = (
            statement.order_by(
                desc(self._posted_date_sort_expr()).nulls_last(),
                Job.scraped_at.desc(),
                Job.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )

        return JobPage(
            jobs=list(self.session.exec(statement).all()),
            total=total,
            page=page,
            pages=pages,
            per_page=per_page,
        )

    def list_sources(self) -> list[str]:
        statement = select(Job.source).distinct().order_by(Job.source)
        return list(self.session.exec(statement).all())

    def dashboard_stats(self) -> DashboardStats:
        sources = list(self.session.exec(select(Source).order_by(Source.slug)).all())
        rows = self.session.exec(
            select(
                Job.source,
                func.count(Job.id),
                func.max(Job.scraped_at),
            ).group_by(Job.source)
        ).all()
        by_source = {
            source: (jobs, last_scraped) for source, jobs, last_scraped in rows
        }

        source_stats: list[SourceJobStats] = []
        for source in sources:
            jobs, last_scraped = by_source.pop(source.slug, (0, None))
            source_stats.append(
                SourceJobStats(
                    source=source.slug,
                    enabled=source.enabled,
                    jobs=jobs,
                    last_scraped_at=last_scraped,
                )
            )

        for source_slug, (jobs, last_scraped) in by_source.items():
            source_stats.append(
                SourceJobStats(
                    source=source_slug,
                    enabled=True,
                    jobs=jobs,
                    last_scraped_at=last_scraped,
                )
            )

        source_stats.sort(key=lambda item: item.source)
        last_scraped_at = max(
            (
                source.last_scraped_at
                for source in source_stats
                if source.last_scraped_at is not None
            ),
            default=None,
        )

        return DashboardStats(
            total_jobs=sum(source.jobs for source in source_stats),
            total_sources=len(source_stats),
            enabled_sources=sum(1 for source in source_stats if source.enabled),
            last_scraped_at=last_scraped_at,
            sources=source_stats,
        )

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

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, and_, cast, desc, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from app.models.common import utcnow
from app.models.job import Job
from app.models.source import Source
from app.models.user_job_delivery import UserJobDelivery
from app.scrapers.base import ScrapedJob


@dataclass(slots=True)
class UpsertResult:
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    inserted_ids: list[int] = field(default_factory=list)


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

    def _posted_date_expr(self):
        raw_data = col(Job.raw_data)
        return func.coalesce(
            raw_data.op("->>")("date_posted"),
            raw_data.op("->>")("date_created"),
        )

    def _posted_date_sort_expr(self):
        return cast(self._posted_date_expr(), DateTime(timezone=True))

    def _posted_within_days_condition(self, days: int):
        cutoff = (utcnow().date() - timedelta(days=days)).isoformat()
        return func.substr(self._posted_date_expr(), 1, 10) >= cutoff

    def list_jobs(
        self,
        filters: JobFilters,
        *,
        page: int = 1,
        per_page: int = 25,
    ) -> JobPage:
        conditions = []
        if filters.q:
            conditions.append(col(Job.title).ilike(f"%{filters.q}%"))
        if filters.source:
            conditions.append(col(Job.source) == filters.source)
        if filters.posted_from or filters.posted_to:
            posted_date = func.substr(self._posted_date_expr(), 1, 10)
            if filters.posted_from:
                conditions.append(posted_date >= filters.posted_from.isoformat())
            if filters.posted_to:
                conditions.append(posted_date <= filters.posted_to.isoformat())

        count_statement = select(func.count(col(Job.id)))
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
                col(Job.scraped_at).desc(),
                col(Job.id).desc(),
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
        statement = select(col(Job.source)).distinct().order_by(col(Job.source))
        return list(self.session.exec(statement).all())

    def list_unread_jobs_for_user(
        self,
        user_id: int,
        *,
        resend_after_seconds: int = 1800,
        limit: int | None = None,
        posted_within_days: int | None = None,
    ) -> list[Job]:
        delivery = aliased(UserJobDelivery)
        cutoff = utcnow() - timedelta(seconds=resend_after_seconds)

        statement = (
            select(Job)
            .outerjoin(
                delivery,
                and_(
                    col(delivery.job_id) == col(Job.id),
                    col(delivery.user_id) == user_id,
                ),
            )
            .where(
                or_(
                    col(delivery.id).is_(None),
                    and_(
                        col(delivery.shown_at).is_(None),
                        or_(
                            col(delivery.sent_at).is_(None),
                            col(delivery.sent_at) < cutoff,
                        ),
                    ),
                )
            )
        )

        if posted_within_days is not None:
            statement = statement.where(
                self._posted_within_days_condition(posted_within_days)
            )

        statement = statement.order_by(
            desc(self._posted_date_sort_expr()).nulls_last(),
            col(Job.scraped_at).desc(),
            col(Job.id).desc(),
        )

        if limit is not None:
            statement = statement.limit(limit)

        return list(self.session.exec(statement).all())

    def list_jobs_for_notification(
        self,
        job_ids: list[int],
        *,
        posted_within_days: int | None = None,
    ) -> list[Job]:
        if not job_ids:
            return []

        statement = select(Job).where(col(Job.id).in_(job_ids))
        if posted_within_days is not None:
            statement = statement.where(
                self._posted_within_days_condition(posted_within_days)
            )
        statement = statement.order_by(
            desc(self._posted_date_sort_expr()).nulls_last(),
            col(Job.scraped_at).desc(),
            col(Job.id).desc(),
        )
        return list(self.session.exec(statement).all())

    def has_shown_history(self, user_id: int) -> bool:
        statement = (
            select(col(UserJobDelivery.id))
            .where(
                col(UserJobDelivery.user_id) == user_id,
                col(UserJobDelivery.shown_at).is_not(None),
            )
            .limit(1)
        )
        return self.session.exec(statement).first() is not None

    def mark_jobs_sent(self, user_id: int, job_ids: list[int]) -> None:
        if not job_ids:
            return

        now = utcnow()
        existing = self._deliveries_for(user_id, job_ids)
        for job_id in job_ids:
            delivery = existing.get(job_id)
            if delivery is None:
                self.session.add(
                    UserJobDelivery(user_id=user_id, job_id=job_id, sent_at=now)
                )
                continue
            delivery.sent_at = now
            self.session.add(delivery)

    def mark_jobs_shown(self, user_id: int, job_ids: list[int]) -> None:
        if not job_ids:
            return

        now = utcnow()
        existing = self._deliveries_for(user_id, job_ids)
        for job_id in job_ids:
            delivery = existing.get(job_id)
            if delivery is None:
                self.session.add(
                    UserJobDelivery(
                        user_id=user_id,
                        job_id=job_id,
                        sent_at=now,
                        shown_at=now,
                    )
                )
                continue
            delivery.shown_at = now
            self.session.add(delivery)

    def _deliveries_for(
        self, user_id: int, job_ids: list[int]
    ) -> dict[int, UserJobDelivery]:
        return {
            delivery.job_id: delivery
            for delivery in self.session.exec(
                select(UserJobDelivery).where(
                    col(UserJobDelivery.user_id) == user_id,
                    col(UserJobDelivery.job_id).in_(job_ids),
                )
            ).all()
        }

    def dashboard_stats(self) -> DashboardStats:
        sources = list(
            self.session.exec(select(Source).order_by(col(Source.slug))).all()
        )
        rows = self.session.exec(
            select(
                col(Job.source),
                func.count(col(Job.id)),
                func.max(col(Job.scraped_at)),
            ).group_by(col(Job.source))
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
                    col(Job.source) == source,
                    col(Job.external_id).in_(list(unique_jobs)),
                )
            ).all()
        }

        scraped_at = utcnow()
        inserted_jobs: list[Job] = []
        for external_id, scraped_job in unique_jobs.items():
            current = existing.get(external_id)
            if current is None:
                job = Job(
                    external_id=external_id,
                    source=source,
                    title=scraped_job.title,
                    raw_data=scraped_job.raw_data,
                    scraped_at=scraped_at,
                )
                self.session.add(job)
                inserted_jobs.append(job)
                result.inserted += 1
                continue

            current.title = scraped_job.title
            current.raw_data = scraped_job.raw_data
            current.scraped_at = scraped_at
            self.session.add(current)
            result.updated += 1

        try:
            self.session.flush()
            result.inserted_ids = [
                job.id for job in inserted_jobs if job.id is not None
            ]
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return result


def serialize_job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "title": job.title,
        "url": job.url,
        "source": job.source,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "scraped_at": job.scraped_at.isoformat(),
    }

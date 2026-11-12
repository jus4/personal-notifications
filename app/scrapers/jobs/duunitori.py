import logging
from typing import Any

from app.core.config import get_settings
from app.models.source import Source
from app.scrapers.base import JobSourceAdapter, ScrapedJob
from app.scrapers.config import resolve_http_profile
from app.scrapers.http import fetch_json
from app.scrapers.registry import register_job_adapter

logger = logging.getLogger(__name__)


@register_job_adapter
class DuunitoriJobAdapter(JobSourceAdapter):
    key = "duunitori_json"

    async def fetch(self, source: Source) -> list[ScrapedJob]:
        if not source.url:
            raise ValueError(f"Source {source.slug!r} has no URL configured")

        profile = resolve_http_profile(source, get_settings(), html=False)
        payload = await fetch_json(source.url, profile=profile)
        if not isinstance(payload, dict):
            raise TypeError("Expected a JSON object from Duunitori")

        results = payload.get("results")
        if not isinstance(results, list):
            raise TypeError("Expected a 'results' list from Duunitori")

        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        for item in results:
            job = self._parse_item(item)
            if job is None:
                continue
            if job.external_id in seen:
                continue
            seen.add(job.external_id)
            jobs.append(job)

        return jobs

    @staticmethod
    def _parse_item(item: Any) -> ScrapedJob | None:
        if not isinstance(item, dict):
            logger.warning("Skipping non-object Duunitori job entry")
            return None

        external_id = item.get("id")
        title = item.get("heading") or item.get("short_heading")
        if external_id is None or not title:
            logger.warning("Skipping Duunitori job entry without id or title")
            return None

        return ScrapedJob(
            external_id=str(external_id),
            title=str(title),
            raw_data=item,
        )

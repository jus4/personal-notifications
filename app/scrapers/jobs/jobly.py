import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from app.models.source import Source
from app.scrapers.base import JobSourceAdapter, ScrapedJob
from app.scrapers.http import HTML_HEADERS, fetch_text
from app.scrapers.registry import register_job_adapter

logger = logging.getLogger(__name__)

MAX_PAGES = 5
BASE_URL = "https://www.jobly.fi"
DATE_FORMAT = "%d.%m.%Y"
EXTERNAL_ID_RE = re.compile(r"(\d+)/?$")


@register_job_adapter
class JoblyJobAdapter(JobSourceAdapter):
    key = "jobly_html"

    async def fetch(self, source: Source) -> list[ScrapedJob]:
        if not source.url:
            raise ValueError(f"Source {source.slug!r} has no URL configured")

        jobs: list[ScrapedJob] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            html = await fetch_text(_with_page(source.url, page), headers=HTML_HEADERS)
            page_jobs = self._parse_page(html)
            if not page_jobs:
                break

            for job in page_jobs:
                if job.external_id in seen:
                    continue
                seen.add(job.external_id)
                jobs.append(job)

        return jobs

    @classmethod
    def _parse_page(cls, html: str) -> list[ScrapedJob]:
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[ScrapedJob] = []

        for article in soup.select("article.node-job"):
            link = article.select_one("h2.node__title a") or article.select_one(
                "a.recruiter-job-link"
            )
            if link is None:
                logger.warning("Skipping Jobly job entry without a link")
                continue

            title = link.get_text(strip=True)
            href = link.get("href")
            if not title or not isinstance(href, str):
                logger.warning("Skipping Jobly job entry without a title or link")
                continue

            external_id = cls._external_id(href)
            if external_id is None:
                logger.warning("Skipping Jobly job entry with unparsable id: %s", href)
                continue

            jobs.append(
                ScrapedJob(
                    external_id=external_id,
                    title=title,
                    raw_data=cls._raw_data(article, _absolute_url(href)),
                )
            )

        return jobs

    @staticmethod
    def _raw_data(article: Tag, link: str) -> dict[str, Any]:
        raw_data: dict[str, Any] = {"link": link}

        date_el = article.select_one(".description .date")
        if date_el is not None:
            date_posted = _parse_date(date_el.get_text(strip=True))
            if date_posted is not None:
                raw_data["date_posted"] = date_posted

        location_el = article.select_one(".location span")
        if location_el is not None:
            location = location_el.get_text(strip=True)
            if location:
                raw_data["location"] = location

        return raw_data

    @staticmethod
    def _external_id(href: str) -> str | None:
        match = EXTERNAL_ID_RE.search(urlsplit(href).path)
        return match.group(1) if match else None


def _parse_date(value: str) -> str | None:
    cleaned = value.strip().rstrip(",.")
    try:
        return datetime.strptime(cleaned, DATE_FORMAT).date().isoformat()
    except ValueError:
        logger.warning("Could not parse Jobly posted date %r", value)
        return None


def _absolute_url(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    return f"{BASE_URL}{href if href.startswith('/') else '/' + href}"


def _with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit(parts._replace(query=urlencode(query)))

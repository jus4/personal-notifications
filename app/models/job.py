from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlparse

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import JSON_TYPE, utcnow


class Job(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(max_length=255)
    source: str = Field(max_length=255)
    title: str = Field(max_length=255)
    scraped_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    raw_data: dict[str, Any] | None = Field(default=None, sa_type=JSON_TYPE)

    @property
    def url(self) -> str | None:
        value = (self.raw_data or {}).get("link")
        if not isinstance(value, str):
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        return value

    @property
    def posted_at(self) -> datetime | None:
        raw_data = self.raw_data or {}
        for key in ("date_posted", "date_created"):
            value = raw_data.get(key)
            if not isinstance(value, str):
                continue
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                continue
        return None

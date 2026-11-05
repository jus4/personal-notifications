from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Boolean, Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.models.common import JSON_TYPE, utcnow


class Source(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "sources"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=255, unique=True)
    kind: str = Field(default="jobs", max_length=255)
    adapter: str = Field(max_length=255)
    url: str | None = Field(default=None, sa_column=Column(Text))
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    config: dict[str, Any] | None = Field(default=None, sa_type=JSON_TYPE)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

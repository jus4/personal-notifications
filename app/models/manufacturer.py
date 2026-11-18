from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.common import utcnow


class Manufacturer(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "manufacturers"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, unique=True)
    slug: str = Field(max_length=255, unique=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

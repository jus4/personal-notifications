from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.common import utcnow


class User(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "users"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, unique=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

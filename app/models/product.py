from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

from app.models.common import utcnow


class Product(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "products"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(max_length=255)
    image_url: str | None = Field(default=None, sa_column=Column(Text))
    manufacturer_id: int | None = Field(
        default=None,
        foreign_key="manufacturers.id",
        index=True,
    )
    category_id: int | None = Field(
        default=None,
        foreign_key="categories.id",
        index=True,
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

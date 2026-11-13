from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import Column, DateTime, Numeric, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import JSON_TYPE, utcnow


class ProductSource(SQLModel, table=True):
    __tablename__: ClassVar[Any] = "product_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "url",
            name="uq_product_sources_source_url",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    url: str = Field(sa_column=Column(Text, nullable=False))
    external_id: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    image_url: str | None = Field(default=None, sa_column=Column(Text))
    price: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(10, 2), nullable=True),
    )
    currency: str | None = Field(default=None, max_length=8)
    availability: str | None = Field(default=None, max_length=32)
    sizes: list[str] = Field(default_factory=list, sa_type=JSON_TYPE)
    raw_data: dict[str, Any] | None = Field(default=None, sa_type=JSON_TYPE)
    last_scraped_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

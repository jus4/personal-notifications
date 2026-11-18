from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import utcnow

SCHEMA_VERSION = 3


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    kind: str = "products"
    adapter: str
    url: str | None = None


class ManufacturerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str


class CategorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str


class ProductSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_slug: str
    url: str
    external_id: str | None = None
    title: str | None = None
    image_url: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    availability: str | None = None
    sizes: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] | None = None
    last_scraped_at: datetime | None = None
    last_error: str | None = None


class ProductSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    image_url: str | None = None
    manufacturer_slug: str | None = None
    category_slug: str | None = None
    offers: list[ProductSourceSnapshot] = Field(default_factory=list)


class ProductSnapshotFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    exported_at: datetime = Field(default_factory=utcnow)
    sources: list[SourceSnapshot] = Field(default_factory=list)
    source: SourceSnapshot | None = Field(default=None, exclude=True)
    manufacturers: list[ManufacturerSnapshot] = Field(default_factory=list)
    categories: list[CategorySnapshot] = Field(default_factory=list)
    products: list[ProductSnapshot] = Field(default_factory=list)

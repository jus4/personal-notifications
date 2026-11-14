from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.models.source import Source

ProductAvailability = Literal["in_stock", "out_of_stock", "unknown"]


class ScrapedJob(BaseModel):
    external_id: str
    title: str
    raw_data: dict[str, Any] | None = None


class ScrapedProduct(BaseModel):
    external_id: str
    title: str
    url: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    availability: ProductAvailability | None = None
    image_url: str | None = None
    sizes: list[str] = Field(default_factory=list)
    raw_data: dict[str, Any] | None = None


class JobSourceAdapter(Protocol):
    key: str

    async def fetch(self, source: Source) -> list[ScrapedJob]: ...


class ProductSourceAdapter(Protocol):
    key: str

    async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]: ...

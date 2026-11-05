from typing import Any, Protocol

from pydantic import BaseModel

from app.models.source import Source


class ScrapedJob(BaseModel):
    external_id: str
    title: str
    raw_data: dict[str, Any] | None = None


class ScrapedProduct(BaseModel):
    external_id: str
    title: str
    raw_data: dict[str, Any] | None = None


class JobSourceAdapter(Protocol):
    key: str

    async def fetch(self, source: Source) -> list[ScrapedJob]: ...


class ProductSourceAdapter(Protocol):
    key: str

    async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]: ...

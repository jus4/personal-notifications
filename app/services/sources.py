import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.models.common import utcnow
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.config import SourceScraperConfig

SOURCE_KINDS = ("jobs", "products")


def parse_source_config(raw: str) -> dict[str, Any] | None:
    cleaned = raw.strip()
    if not cleaned:
        return None

    try:
        config = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg}") from exc

    if not isinstance(config, dict):
        raise TypeError("Config must be a JSON object")

    try:
        SourceScraperConfig.model_validate(config)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    return config


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_sources(self, kind: str | None = None) -> list[Source]:
        statement = select(Source)
        if kind is not None:
            statement = statement.where(col(Source.kind) == kind)
        statement = statement.order_by(col(Source.kind), col(Source.slug))
        return list(self.session.exec(statement).all())

    def get_source(self, source_id: int) -> Source | None:
        return self.session.get(Source, source_id)

    def get_source_by_slug(self, slug: str) -> Source | None:
        statement = select(Source).where(col(Source.slug) == slug)
        return self.session.exec(statement).first()

    def save_source(
        self,
        *,
        source_id: int | None,
        slug: str,
        kind: str,
        adapter: str,
        url: str | None,
        enabled: bool,
        config: dict[str, Any] | None,
    ) -> Source:
        cleaned_slug = slug.strip()
        if not cleaned_slug:
            raise ValueError("Slug is required")

        now = utcnow()
        if source_id is None:
            if self.get_source_by_slug(cleaned_slug) is not None:
                raise ValueError("A source with this slug already exists")
            source = Source(
                slug=cleaned_slug,
                kind=kind,
                adapter=adapter,
                url=url,
                enabled=enabled,
                config=config,
                created_at=now,
                updated_at=now,
            )
        else:
            source = self.get_source(source_id)
            if source is None:
                raise ValueError("Source not found")
            existing = self.get_source_by_slug(cleaned_slug)
            if existing is not None and existing.id != source_id:
                raise ValueError("A source with this slug already exists")
            if source.kind != kind and self.has_product_links(source_id):
                raise ValueError(
                    "Cannot change the kind of a source with linked product offers"
                )

            source.slug = cleaned_slug
            source.kind = kind
            source.adapter = adapter
            source.url = url
            source.enabled = enabled
            source.config = config
            source.updated_at = now

        self.session.add(source)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ValueError("A source with this slug already exists") from exc
        self.session.refresh(source)
        return source

    def has_product_links(self, source_id: int) -> bool:
        statement = (
            select(col(ProductSource.id))
            .where(col(ProductSource.source_id) == source_id)
            .limit(1)
        )
        return self.session.exec(statement).first() is not None

    def delete_source(self, source: Source) -> None:
        if source.id is not None and self.has_product_links(source.id):
            raise ValueError(
                "Source has linked product offers; unlink them before deleting"
            )
        self.session.delete(source)
        self.session.commit()

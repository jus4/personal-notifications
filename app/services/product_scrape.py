import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from sqlmodel import Session, col, select

from app.core.config import get_settings
from app.models.common import utcnow
from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ProductSourceAdapter, ScrapedProduct
from app.scrapers.registry import PRODUCT_ADAPTERS
from app.services.products import ProductRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProductScrapeResult:
    source: str
    links: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProductScrapeFailure:
    source: str
    error: str


@dataclass(slots=True)
class ProductScrapeBatchResult:
    results: list[ProductScrapeResult] = field(default_factory=list)
    failures: list[ProductScrapeFailure] = field(default_factory=list)
    source_count: int = 0


@dataclass(slots=True)
class ProductMatchResult:
    matched: bool
    reason: str


def get_product_adapter(source: Source) -> ProductSourceAdapter:
    adapter_cls = PRODUCT_ADAPTERS.get(source.adapter)
    if adapter_cls is None:
        raise ValueError(
            f"No product adapter registered for {source.adapter!r} "
            f"(source {source.slug!r})"
        )
    return adapter_cls()


async def fetch_product_preview(source: Source, url: str) -> ScrapedProduct:
    adapter = get_product_adapter(source)
    products = await adapter.fetch(source, url)
    if not products:
        raise ValueError("Adapter returned no products")
    if len(products) > 1:
        logger.warning(
            "Product adapter %s returned %s products for %s; using the first",
            source.adapter,
            len(products),
            url,
        )
    return products[0]


def compare_scraped_product(
    product: Product,
    link: ProductSource,
    scraped: ScrapedProduct,
) -> ProductMatchResult:
    existing_external_id = (link.external_id or "").strip()
    scraped_external_id = (scraped.external_id or "").strip()
    if existing_external_id and existing_external_id == scraped_external_id:
        return ProductMatchResult(matched=True, reason="external_id")

    if _titles_match(product.title, scraped.title):
        return ProductMatchResult(matched=True, reason="title")

    return ProductMatchResult(matched=False, reason="mismatch")


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^0-9a-z]+", " ", normalized.casefold())
    return " ".join(normalized.split())


def _titles_match(linked_title: str, scraped_title: str) -> bool:
    linked = _normalize_title(linked_title)
    scraped = _normalize_title(scraped_title)
    if not linked or not scraped:
        return False

    linked_tokens = linked.split()
    scraped_tokens = set(scraped.split())
    if all(token in scraped_tokens for token in linked_tokens):
        return True

    return SequenceMatcher(None, linked, scraped).ratio() >= 0.8


async def _scrape_links(
    session: Session,
    repository: ProductRepository,
    adapter: ProductSourceAdapter,
    source: Source,
    links: list[ProductSource],
    result: ProductScrapeResult,
) -> None:
    for link in links:
        link_id = link.id
        if link_id is None:
            continue
        link_url = link.url

        try:
            products = await adapter.fetch(source, link_url)
            if not products:
                raise ValueError("Adapter returned no products")
            if len(products) > 1:
                logger.warning(
                    "Product adapter %s returned %s products for %s; using the first",
                    source.adapter,
                    len(products),
                    link_url,
                )
            scraped = products[0]
            repository.update_scraped_source(
                link,
                scraped,
                scraped_at=utcnow(),
            )
            session.commit()
            result.updated += 1
        except Exception as exc:
            session.rollback()
            logger.exception("Product scrape failed for %s", link_url)
            failed_link = session.get(ProductSource, link_id)
            if failed_link is not None:
                repository.mark_source_error(
                    failed_link,
                    str(exc),
                    updated_at=utcnow(),
                )
                session.commit()
            result.failed += 1
            result.errors.append(f"{link_url}: {exc}")


async def run_product_source(
    session: Session,
    source: Source,
    *,
    force: bool = False,
    due_before: datetime | None = None,
) -> ProductScrapeResult:
    adapter_cls = PRODUCT_ADAPTERS.get(source.adapter)
    if adapter_cls is None:
        raise ValueError(
            f"No product adapter registered for {source.adapter!r} "
            f"(source {source.slug!r})"
        )

    adapter: ProductSourceAdapter = adapter_cls()
    repository = ProductRepository(session)
    links = repository.list_source_links(
        source.id if source.id is not None else -1,
        due_before=None if force else due_before,
    )
    result = ProductScrapeResult(source=source.slug, links=len(links))
    await _scrape_links(session, repository, adapter, source, links, result)
    return result


async def run_product_offers(
    session: Session,
    product: Product,
    *,
    force: bool = True,
    interval_seconds: int | None = None,
) -> ProductScrapeBatchResult:
    if interval_seconds is None:
        interval_seconds = get_settings().product_scrape_interval_seconds

    due_before = None if force else utcnow() - timedelta(seconds=interval_seconds)

    repository = ProductRepository(session)
    links = repository.list_product_links(
        product.id if product.id is not None else -1,
        due_before=due_before,
    )

    grouped: dict[int, list[ProductSource]] = {}
    for link in links:
        grouped.setdefault(link.source_id, []).append(link)

    batch = ProductScrapeBatchResult(source_count=len(grouped))
    for source_id, source_links in grouped.items():
        source = session.get(Source, source_id)
        if source is None:
            continue

        adapter_cls = PRODUCT_ADAPTERS.get(source.adapter)
        if adapter_cls is None:
            batch.failures.append(
                ProductScrapeFailure(
                    source=source.slug,
                    error=(
                        f"No product adapter registered for {source.adapter!r} "
                        f"(source {source.slug!r})"
                    ),
                )
            )
            continue

        adapter: ProductSourceAdapter = adapter_cls()
        result = ProductScrapeResult(source=source.slug, links=len(source_links))
        await _scrape_links(
            session,
            repository,
            adapter,
            source,
            source_links,
            result,
        )
        batch.results.append(result)

    return batch


async def run_product_scrape(
    session: Session,
    *,
    force: bool = False,
    source_slug: str | None = None,
    interval_seconds: int | None = None,
) -> ProductScrapeBatchResult:
    if interval_seconds is None:
        interval_seconds = get_settings().product_scrape_interval_seconds

    due_before = None if force else utcnow() - timedelta(seconds=interval_seconds)

    statement = select(Source).where(
        col(Source.enabled).is_(True),
        col(Source.kind) == "products",
    )
    if source_slug:
        statement = statement.where(col(Source.slug) == source_slug)
    sources = list(session.exec(statement.order_by(col(Source.slug))).all())

    batch = ProductScrapeBatchResult(source_count=len(sources))
    for source in sources:
        slug = source.slug
        try:
            result = await run_product_source(
                session,
                source,
                force=force,
                due_before=due_before,
            )
            batch.results.append(result)
        except Exception as exc:
            session.rollback()
            logger.exception("Product scrape failed for %s", slug)
            batch.failures.append(ProductScrapeFailure(source=slug, error=str(exc)))

    return batch

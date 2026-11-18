import logging
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, col, select

from app.models.category import Category
from app.models.common import utcnow
from app.models.manufacturer import Manufacturer
from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.schemas.product_snapshot import (
    SCHEMA_VERSION,
    CategorySnapshot,
    ManufacturerSnapshot,
    ProductSnapshot,
    ProductSnapshotFile,
    ProductSourceSnapshot,
    SourceSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestReport:
    source_created: bool = False
    manufacturers_created: int = 0
    categories_created: int = 0
    products_created: int = 0
    products_updated: int = 0
    offers_created: int = 0
    offers_updated: int = 0
    dry_run: bool = False


def export_product_snapshot(
    session: Session,
    *,
    source_slug: str | None = None,
    source_slugs: list[str] | None = None,
    manufacturer_slugs: list[str] | None = None,
) -> ProductSnapshotFile:
    requested_slugs = source_slugs
    if requested_slugs is None and source_slug is not None:
        requested_slugs = [source_slug]
    sources = _select_sources(session, requested_slugs)
    source_by_id = {source.id: source for source in sources if source.id is not None}
    if not source_by_id:
        raise ValueError("No product sources matched for export")

    statement = (
        select(Product, Manufacturer, Category, ProductSource)
        .join(
            ProductSource,
            col(ProductSource.product_id) == col(Product.id),
        )
        .outerjoin(
            Manufacturer,
            col(Product.manufacturer_id) == col(Manufacturer.id),
        )
        .outerjoin(
            Category,
            col(Product.category_id) == col(Category.id),
        )
        .where(col(ProductSource.source_id).in_(list(source_by_id)))
        .order_by(col(Product.id), col(ProductSource.id))
    )
    if manufacturer_slugs:
        statement = statement.where(col(Manufacturer.slug).in_(manufacturer_slugs))

    rows = [tuple(row) for row in session.exec(statement).all()]
    products: dict[int, ProductSnapshot] = {}
    manufacturers: dict[str, ManufacturerSnapshot] = {}
    categories: dict[str, CategorySnapshot] = {}
    used_sources: dict[str, SourceSnapshot] = {}

    for product, manufacturer, category, link in rows:
        if product.id is None:
            continue
        source = source_by_id.get(link.source_id)
        if source is None:
            raise ValueError(
                f"Offer {link.url!r} references an unselected product source"
            )

        snapshot = products.get(product.id)
        if snapshot is None:
            manufacturer_slug = manufacturer.slug if manufacturer else None
            category_slug = category.slug if category else None
            if manufacturer is not None:
                manufacturers[manufacturer.slug] = ManufacturerSnapshot(
                    slug=manufacturer.slug,
                    name=manufacturer.name,
                )
            if category is not None:
                categories[category.slug] = CategorySnapshot(
                    slug=category.slug,
                    name=category.name,
                )
            snapshot = ProductSnapshot(
                title=product.title,
                image_url=product.image_url,
                manufacturer_slug=manufacturer_slug,
                category_slug=category_slug,
                offers=[],
            )
            products[product.id] = snapshot

        used_sources[source.slug] = _source_definition(source)
        snapshot.offers.append(_source_snapshot(source, link))

    return ProductSnapshotFile(
        schema_version=SCHEMA_VERSION,
        exported_at=utcnow(),
        sources=sorted(
            used_sources.values(),
            key=lambda source: source.slug,
        ),
        manufacturers=sorted(
            manufacturers.values(),
            key=lambda manufacturer: manufacturer.slug,
        ),
        categories=sorted(
            categories.values(),
            key=lambda category: category.slug,
        ),
        products=[products[product_id] for product_id in sorted(products)],
    )


def write_product_snapshot(
    snapshot: ProductSnapshotFile,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        snapshot.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def read_product_snapshot(path: Path) -> ProductSnapshotFile:
    return ProductSnapshotFile.model_validate_json(path.read_text(encoding="utf-8"))


def ingest_product_snapshot(
    session: Session,
    snapshot: ProductSnapshotFile,
    *,
    dry_run: bool = False,
) -> IngestReport:
    if snapshot.schema_version not in {2, SCHEMA_VERSION}:
        raise ValueError(
            f"Unsupported snapshot schema version {snapshot.schema_version}; "
            f"expected 2 or {SCHEMA_VERSION}"
        )

    snapshot_sources = _snapshot_sources(snapshot)
    _validate_snapshot(snapshot, snapshot_sources)
    report = IngestReport(dry_run=dry_run)

    try:
        sources: dict[str, Source] = {}
        for source_data in snapshot_sources:
            source, created = _ensure_source(session, source_data)
            if source.id is None:
                raise ValueError("Source must be persisted")
            sources[source.slug] = source
            report.source_created = report.source_created or created

        manufacturers = _upsert_manufacturers(
            session,
            snapshot.manufacturers,
            report,
        )
        categories = _upsert_categories(
            session,
            snapshot.categories,
            report,
        )
        _validate_manufacturers(snapshot, manufacturers)
        _validate_categories(snapshot, categories)

        for product_snapshot in snapshot.products:
            _ingest_product(
                session,
                sources,
                product_snapshot,
                manufacturers,
                categories,
                report,
            )

        if dry_run:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise

    return report


def _source_snapshot(
    source: Source,
    link: ProductSource,
) -> ProductSourceSnapshot:
    return ProductSourceSnapshot(
        source_slug=source.slug,
        url=link.url,
        external_id=link.external_id,
        title=link.title,
        image_url=link.image_url,
        price=link.price,
        currency=link.currency,
        availability=link.availability,
        sizes=list(link.sizes or []),
        raw_data=link.raw_data,
        last_scraped_at=link.last_scraped_at,
        last_error=link.last_error,
    )


def _validate_snapshot(
    snapshot: ProductSnapshotFile,
    sources: list[SourceSnapshot],
) -> None:
    if not sources:
        raise ValueError("Snapshot has no product sources")

    source_slugs = {source.slug for source in sources}
    seen_offers: set[tuple[str, str]] = set()
    for product in snapshot.products:
        if not product.offers:
            raise ValueError(f"Product {product.title!r} has no offers")
        for offer in product.offers:
            if offer.source_slug not in source_slugs:
                raise ValueError(
                    f"Offer {offer.url!r} references unknown source "
                    f"{offer.source_slug!r}"
                )
            key = (offer.source_slug, offer.url)
            if key in seen_offers:
                raise ValueError(
                    f"Duplicate offer in snapshot: {offer.source_slug!r} {offer.url!r}"
                )
            seen_offers.add(key)


def _snapshot_sources(snapshot: ProductSnapshotFile) -> list[SourceSnapshot]:
    if snapshot.sources:
        return snapshot.sources
    if snapshot.source is not None:
        return [snapshot.source]
    return []


def _ensure_source(
    session: Session,
    source_data: SourceSnapshot,
) -> tuple[Source, bool]:
    source = session.exec(
        select(Source).where(col(Source.slug) == source_data.slug)
    ).first()
    if source is not None:
        if source.kind != "products":
            raise ValueError(f"Source {source_data.slug!r} is not a product source")
        return source, False

    now = utcnow()
    source = Source(
        slug=source_data.slug,
        kind=source_data.kind,
        adapter=source_data.adapter,
        url=source_data.url,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    session.add(source)
    session.flush()
    return source, True


def _select_sources(
    session: Session,
    source_slugs: list[str] | None,
) -> list[Source]:
    statement = select(Source).where(col(Source.kind) == "products")
    if source_slugs:
        statement = statement.where(col(Source.slug).in_(source_slugs))
    statement = statement.order_by(col(Source.slug))
    sources = list(session.exec(statement).all())
    if source_slugs:
        found = {source.slug for source in sources}
        missing = [slug for slug in source_slugs if slug not in found]
        if missing:
            raise ValueError(f"Product sources not found: {', '.join(missing)}")
    return sources


def _source_definition(source: Source) -> SourceSnapshot:
    return SourceSnapshot(
        slug=source.slug,
        kind=source.kind,
        adapter=source.adapter,
        url=source.url,
    )


def _upsert_manufacturers(
    session: Session,
    snapshots: list[ManufacturerSnapshot],
    report: IngestReport,
) -> dict[str, Manufacturer]:
    manufacturers: dict[str, Manufacturer] = {}
    now = utcnow()

    for item in snapshots:
        manufacturer = session.exec(
            select(Manufacturer).where(col(Manufacturer.slug) == item.slug)
        ).first()
        if manufacturer is None:
            manufacturer = Manufacturer(
                name=item.name,
                slug=item.slug,
                created_at=now,
                updated_at=now,
            )
            session.add(manufacturer)
            report.manufacturers_created += 1
        elif manufacturer.name != item.name:
            manufacturer.name = item.name
            manufacturer.updated_at = now
            session.add(manufacturer)
        manufacturers[item.slug] = manufacturer

    session.flush()
    return manufacturers


def _upsert_categories(
    session: Session,
    snapshots: list[CategorySnapshot],
    report: IngestReport,
) -> dict[str, Category]:
    categories: dict[str, Category] = {}
    now = utcnow()

    for item in snapshots:
        category = session.exec(
            select(Category).where(col(Category.slug) == item.slug)
        ).first()
        if category is None:
            category = Category(
                name=item.name,
                slug=item.slug,
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            report.categories_created += 1
        elif category.name != item.name:
            category.name = item.name
            category.updated_at = now
            session.add(category)
        categories[item.slug] = category

    session.flush()
    return categories


def _validate_manufacturers(
    snapshot: ProductSnapshotFile,
    manufacturers: dict[str, Manufacturer],
) -> None:
    for product in snapshot.products:
        if (
            product.manufacturer_slug is not None
            and product.manufacturer_slug not in manufacturers
        ):
            raise ValueError(
                f"Product {product.title!r} references unknown manufacturer "
                f"{product.manufacturer_slug!r}"
            )


def _validate_categories(
    snapshot: ProductSnapshotFile,
    categories: dict[str, Category],
) -> None:
    for product in snapshot.products:
        if (
            product.category_slug is not None
            and product.category_slug not in categories
        ):
            raise ValueError(
                f"Product {product.title!r} references unknown category "
                f"{product.category_slug!r}"
            )


def _ingest_product(
    session: Session,
    sources: dict[str, Source],
    snapshot: ProductSnapshot,
    manufacturers: dict[str, Manufacturer],
    categories: dict[str, Category],
    report: IngestReport,
) -> None:
    existing_links: list[ProductSource | None] = []
    offer_sources: list[Source] = []
    for offer in snapshot.offers:
        source = sources.get(offer.source_slug)
        if source is None or source.id is None:
            raise ValueError(
                f"Offer {offer.url!r} references unknown source {offer.source_slug!r}"
            )
        offer_sources.append(source)
        existing_links.append(
            session.exec(
                select(ProductSource).where(
                    col(ProductSource.source_id) == source.id,
                    col(ProductSource.url) == offer.url,
                )
            ).first()
        )

    product_ids = {link.product_id for link in existing_links if link is not None}
    if len(product_ids) > 1:
        raise ValueError(
            f"Offers for product {snapshot.title!r} point to multiple products"
        )

    product: Product | None = None
    if product_ids:
        product = session.get(Product, product_ids.pop())
    if product is None:
        manufacturer = (
            manufacturers.get(snapshot.manufacturer_slug)
            if snapshot.manufacturer_slug
            else None
        )
        category = (
            categories.get(snapshot.category_slug) if snapshot.category_slug else None
        )
        now = utcnow()
        product = Product(
            title=snapshot.title,
            image_url=snapshot.image_url,
            manufacturer_id=manufacturer.id if manufacturer else None,
            category_id=category.id if category else None,
            created_at=now,
            updated_at=now,
        )
        session.add(product)
        session.flush()
        report.products_created += 1
    else:
        _refresh_product(product, snapshot, manufacturers, categories)
        report.products_updated += 1

    if product.id is None:
        raise ValueError("Product must be persisted")

    for offer, link, source in zip(
        snapshot.offers,
        existing_links,
        offer_sources,
        strict=True,
    ):
        if source.id is None:
            raise ValueError("Source must be persisted")
        if link is None:
            now = utcnow()
            link = ProductSource(
                product_id=product.id,
                source_id=source.id,
                url=offer.url,
                sizes=[],
                created_at=now,
                updated_at=now,
            )
            session.add(link)
            report.offers_created += 1
        else:
            report.offers_updated += 1
        _apply_offer(link, offer)


def _refresh_product(
    product: Product,
    snapshot: ProductSnapshot,
    manufacturers: dict[str, Manufacturer],
    categories: dict[str, Category],
) -> None:
    manufacturer = (
        manufacturers.get(snapshot.manufacturer_slug)
        if snapshot.manufacturer_slug
        else None
    )
    category = (
        categories.get(snapshot.category_slug) if snapshot.category_slug else None
    )
    changed = False
    if product.manufacturer_id is None and manufacturer is not None:
        product.manufacturer_id = manufacturer.id
        changed = True
    if product.category_id is None and category is not None:
        product.category_id = category.id
        changed = True
    if product.image_url is None and snapshot.image_url is not None:
        product.image_url = snapshot.image_url
        changed = True
    if changed:
        product.updated_at = utcnow()


def _apply_offer(
    link: ProductSource,
    offer: ProductSourceSnapshot,
) -> None:
    link.external_id = offer.external_id
    link.title = offer.title
    link.image_url = offer.image_url
    link.price = offer.price
    link.currency = offer.currency
    link.availability = offer.availability
    link.sizes = list(offer.sizes)
    link.raw_data = offer.raw_data
    link.last_scraped_at = offer.last_scraped_at
    link.last_error = offer.last_error
    link.updated_at = offer.last_scraped_at or utcnow()

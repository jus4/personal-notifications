from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlmodel import Session, select

from app.models.category import Category
from app.models.manufacturer import Manufacturer
from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.schemas.product_snapshot import ProductSnapshotFile
from app.services.product_snapshot import (
    export_product_snapshot,
    ingest_product_snapshot,
    read_product_snapshot,
    write_product_snapshot,
)

SOURCE_SLUG = "bergfreunde"
PRODUCT_URL = "https://www.bergfreunde.fi/la-sportiva-solution-kiipeilykengaet/"
SCRAPED_AT = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _seed_product(session: Session) -> None:
    manufacturer = Manufacturer(name="La Sportiva", slug="la-sportiva")
    category = Category(name="Climbing shoes", slug="climbing-shoes")
    source = Source(
        slug=SOURCE_SLUG,
        kind="products",
        adapter="bergfreunde_html",
        enabled=True,
    )
    session.add(manufacturer)
    session.add(category)
    session.add(source)
    session.commit()
    session.refresh(manufacturer)
    session.refresh(category)
    session.refresh(source)

    product = Product(
        title="La Sportiva Solution",
        image_url="https://cdn.example.test/solution.jpg",
        manufacturer_id=manufacturer.id,
        category_id=category.id,
    )
    session.add(product)
    session.commit()
    session.refresh(product)

    link = ProductSource(
        product_id=product.id or 0,
        source_id=source.id or 0,
        url=PRODUCT_URL,
        external_id="301-0448",
        title="La Sportiva - Solution - Kiipeilykengät",
        image_url="https://cdn.example.test/offer.jpg",
        price=Decimal("131.96"),
        currency="EUR",
        availability="in_stock",
        sizes=["40", "41"],
        raw_data={"link": PRODUCT_URL},
        last_scraped_at=SCRAPED_AT,
    )
    session.add(link)
    session.commit()


def _clear_products(session: Session) -> None:
    session.execute(delete(ProductSource))
    session.execute(delete(Product))
    session.execute(delete(Manufacturer))
    session.execute(delete(Category))
    session.execute(delete(Source))
    session.commit()


def test_export_product_snapshot_round_trip(
    session: Session,
    tmp_path: Path,
) -> None:
    _seed_product(session)

    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)

    assert snapshot.schema_version == 3
    assert snapshot.sources[0].slug == SOURCE_SLUG
    assert snapshot.manufacturers[0].slug == "la-sportiva"
    assert snapshot.categories[0].slug == "climbing-shoes"
    assert len(snapshot.products) == 1
    product = snapshot.products[0]
    assert product.title == "La Sportiva Solution"
    assert product.manufacturer_slug == "la-sportiva"
    assert product.category_slug == "climbing-shoes"
    assert product.offers[0].url == PRODUCT_URL
    assert product.offers[0].price == Decimal("131.96")

    path = tmp_path / "products.json"
    write_product_snapshot(snapshot, path)
    loaded = read_product_snapshot(path)

    assert loaded == snapshot


def test_ingest_creates_data_without_network(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    _clear_products(session)

    report = ingest_product_snapshot(session, snapshot)

    assert report.source_created is True
    assert report.manufacturers_created == 1
    assert report.categories_created == 1
    assert report.products_created == 1
    assert report.offers_created == 1

    product = session.exec(select(Product)).one()
    link = session.exec(select(ProductSource)).one()
    manufacturer = session.exec(select(Manufacturer)).one()
    category = session.exec(select(Category)).one()
    assert product.title == "La Sportiva Solution"
    assert product.manufacturer_id == manufacturer.id
    assert product.category_id == category.id
    assert link.url == PRODUCT_URL
    assert link.price == Decimal("131.96")
    assert link.sizes == ["40", "41"]


def test_ingest_is_idempotent_and_refreshes_offers(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    _clear_products(session)

    ingest_product_snapshot(session, snapshot)

    product = session.exec(select(Product)).one()
    product.title = "Custom Launch Name"
    product.category_id = None
    session.add(product)
    session.commit()

    refreshed = snapshot.model_copy(deep=True)
    refreshed.products[0].offers[0].price = Decimal("99.00")
    report = ingest_product_snapshot(session, refreshed)

    assert report.products_created == 0
    assert report.offers_created == 0
    assert report.categories_created == 0
    assert report.products_updated == 1
    assert report.offers_updated == 1
    assert len(session.exec(select(Product)).all()) == 1
    assert len(session.exec(select(ProductSource)).all()) == 1
    product = session.exec(select(Product)).one()
    link = session.exec(select(ProductSource)).one()
    category = session.exec(select(Category)).one()
    assert product.title == "Custom Launch Name"
    assert product.category_id == category.id
    assert link.price == Decimal("99.00")


def test_ingest_dry_run_rolls_back(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    _clear_products(session)

    report = ingest_product_snapshot(session, snapshot, dry_run=True)

    assert report.dry_run is True
    assert report.products_created == 1
    assert report.categories_created == 1
    assert session.exec(select(Product)).all() == []
    assert session.exec(select(Manufacturer)).all() == []
    assert session.exec(select(Category)).all() == []


def test_ingest_rejects_unsupported_schema_version(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    snapshot.schema_version = 999

    with pytest.raises(ValueError, match="Unsupported snapshot schema version"):
        ingest_product_snapshot(session, snapshot)


def test_ingest_rejects_v1_snapshot(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    snapshot.schema_version = 1

    with pytest.raises(ValueError, match="expected 2"):
        ingest_product_snapshot(session, snapshot)


def test_ingest_rejects_duplicate_offer_urls(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    duplicate = snapshot.products[0].model_copy(deep=True)
    duplicate.title = "Duplicate"

    invalid = ProductSnapshotFile(
        schema_version=3,
        sources=snapshot.sources,
        manufacturers=snapshot.manufacturers,
        categories=snapshot.categories,
        products=[snapshot.products[0], duplicate],
    )

    with pytest.raises(ValueError, match="Duplicate offer"):
        ingest_product_snapshot(session, invalid)


def test_export_and_ingest_multi_source_snapshot(session: Session) -> None:
    _seed_product(session)
    product = session.exec(select(Product)).one()
    varuste = Source(
        slug="varuste",
        kind="products",
        adapter="varuste_html",
        enabled=True,
    )
    session.add(varuste)
    session.commit()
    session.refresh(varuste)
    varuste_url = "https://varuste.net/p85496/la-sportiva-solution"
    session.add(
        ProductSource(
            product_id=product.id or 0,
            source_id=varuste.id or 0,
            url=varuste_url,
            price=Decimal("139.90"),
            currency="EUR",
        )
    )
    session.commit()

    snapshot = export_product_snapshot(
        session,
        source_slugs=[SOURCE_SLUG, "varuste"],
    )

    assert snapshot.schema_version == 3
    assert {source.slug for source in snapshot.sources} == {
        SOURCE_SLUG,
        "varuste",
    }
    assert len(snapshot.products) == 1
    assert {offer.url for offer in snapshot.products[0].offers} == {
        PRODUCT_URL,
        varuste_url,
    }

    _clear_products(session)
    report = ingest_product_snapshot(session, snapshot)

    assert report.products_created == 1
    assert report.offers_created == 2
    assert {link.url for link in session.exec(select(ProductSource)).all()} == {
        PRODUCT_URL,
        varuste_url,
    }


def test_ingest_accepts_legacy_v2_snapshot(session: Session) -> None:
    _seed_product(session)
    snapshot = export_product_snapshot(session, source_slug=SOURCE_SLUG)
    legacy = snapshot.model_copy(deep=True)
    legacy.schema_version = 2
    legacy.source = legacy.sources[0]
    legacy.sources = []
    _clear_products(session)

    report = ingest_product_snapshot(session, legacy)

    assert report.source_created is True
    assert report.products_created == 1
    assert report.offers_created == 1
    assert len(session.exec(select(ProductSource)).all()) == 1

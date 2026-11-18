from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app import cli
from app.models.category import Category
from app.models.manufacturer import Manufacturer
from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.services.product_snapshot import read_product_snapshot


def test_sources_add_creates_and_updates(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "https://example.test/one",
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "duunitori")).one()
        source_id = source.id
        assert source.kind == "jobs"
        assert source.adapter == "duunitori_json"
        assert source.url == "https://example.test/one"
        assert source.enabled is True

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "https://example.test/two",
                "--disabled",
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "duunitori")).one()
        assert source.id == source_id
        assert source.url == "https://example.test/two"
        assert source.enabled is False


def test_sources_add_rejects_url_without_scheme(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    with pytest.raises(SystemExit):
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
                "--url",
                "example.test/jobs",
            ]
        )


def test_sources_add_stores_scraper_config(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "jobly",
                "--adapter",
                "jobly_html",
                "--url",
                "https://example.test/jobly",
                "--config-json",
                '{"http": {"impersonate": "none"}}',
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "jobly")).one()
        assert source.config == {"http": {"impersonate": "none"}}


def test_sources_add_accepts_product_source_without_url(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    assert (
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "bergfreunde",
                "--kind",
                "products",
                "--adapter",
                "bergfreunde_html",
            ]
        )
        == 0
    )

    with Session(engine) as session:
        source = session.exec(select(Source).where(Source.slug == "bergfreunde")).one()
        assert source.kind == "products"
        assert source.url is None


def test_sources_add_requires_url_for_jobs(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    with pytest.raises(SystemExit):
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "duunitori",
                "--adapter",
                "duunitori_json",
            ]
        )


def test_sources_add_rejects_invalid_scraper_config(
    monkeypatch: Any,
    engine: Engine,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    with pytest.raises(SystemExit):
        cli.main(
            [
                "sources",
                "add",
                "--slug",
                "jobly",
                "--adapter",
                "jobly_html",
                "--url",
                "https://example.test/jobly",
                "--config-json",
                '{"http": {"unknown": true}}',
            ]
        )


def test_products_export_and_ingest(
    monkeypatch: Any,
    engine: Engine,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "engine", engine)

    with Session(engine) as session:
        manufacturer = Manufacturer(name="La Sportiva", slug="la-sportiva")
        category = Category(name="Climbing shoes", slug="climbing-shoes")
        source = Source(
            slug="bergfreunde",
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
            manufacturer_id=manufacturer.id,
            category_id=category.id,
            image_url="https://cdn.example.test/solution.jpg",
        )
        session.add(product)
        session.commit()
        session.refresh(product)

        session.add(
            ProductSource(
                product_id=product.id or 0,
                source_id=source.id or 0,
                url="https://www.bergfreunde.fi/solution/",
                price=Decimal("99.90"),
                currency="EUR",
                sizes=["40"],
            )
        )
        session.commit()

    output = tmp_path / "snapshot.json"

    assert (
        cli.main(
            [
                "products",
                "export",
                "--source",
                "bergfreunde",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    snapshot = read_product_snapshot(output)
    assert snapshot.schema_version == 3
    assert snapshot.products[0].title == "La Sportiva Solution"
    assert snapshot.products[0].category_slug == "climbing-shoes"
    assert snapshot.products[0].offers[0].price == Decimal("99.90")

    with Session(engine) as session:
        session.execute(delete(ProductSource))
        session.execute(delete(Product))
        session.execute(delete(Manufacturer))
        session.execute(delete(Category))
        session.execute(delete(Source))
        session.commit()

    assert (
        cli.main(
            [
                "products",
                "ingest",
                "--input",
                str(output),
            ]
        )
        == 0
    )

    with Session(engine) as session:
        product = session.exec(select(Product)).one()
        manufacturer = session.exec(select(Manufacturer)).one()
        category = session.exec(select(Category)).one()
        link = session.exec(select(ProductSource)).one()
        assert product.title == "La Sportiva Solution"
        assert product.manufacturer_id == manufacturer.id
        assert product.category_id == category.id
        assert link.price == Decimal("99.90")

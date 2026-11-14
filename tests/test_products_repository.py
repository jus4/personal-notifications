from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ScrapedProduct
from app.services.products import ProductRepository


def _shop(session: Session, slug: str = "bergfreunde") -> Source:
    source = Source(
        slug=slug,
        kind="products",
        adapter="bergfreunde_html",
        enabled=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def test_create_product_requires_title(session: Session) -> None:
    with pytest.raises(ValueError, match="title is required"):
        ProductRepository(session).create_product(title="   ")


def test_add_product_source_validates_shop_and_duplicates(
    session: Session,
) -> None:
    repository = ProductRepository(session)
    product = repository.create_product(title="La Sportiva Solution")
    shop = _shop(session)
    url = "https://www.bergfreunde.fi/la-sportiva-solution-kiipeilykengaet/"

    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url=url,
    )

    assert link.product_id == product.id
    assert link.source_id == shop.id
    assert link.sizes == []

    with pytest.raises(ValueError, match="already linked"):
        repository.add_product_source(
            product_id=product.id or 0,
            source_id=shop.id or 0,
            url=url,
        )

    job_shop = Source(
        slug="job-shop",
        kind="jobs",
        adapter="jobly_html",
        url="https://example.test/jobs",
    )
    session.add(job_shop)
    session.commit()
    session.refresh(job_shop)

    with pytest.raises(ValueError, match="Product source not found"):
        repository.add_product_source(
            product_id=product.id or 0,
            source_id=job_shop.id or 0,
            url="https://example.test/product",
        )


def test_update_scraped_source_and_product_detail(session: Session) -> None:
    repository = ProductRepository(session)
    product = repository.create_product(title="La Sportiva Solution")
    shop = _shop(session)
    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://www.bergfreunde.fi/la-sportiva-solution-kiipeilykengaet/",
    )
    scraped_at = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    repository.update_scraped_source(
        link,
        ScrapedProduct(
            external_id="301-0448",
            title="La Sportiva - Solution - Kiipeilykengät",
            url=link.url,
            price=Decimal("131.96"),
            currency="EUR",
            availability="in_stock",
            image_url="https://example.test/large.jpg",
            sizes=["34", "34,5", "35"],
            raw_data={"link": link.url},
        ),
        scraped_at=scraped_at,
    )
    session.commit()

    detail = repository.get_product_detail(product.id or 0)

    assert detail is not None
    assert detail.lowest_price == Decimal("131.96")
    assert detail.currency == "EUR"
    assert detail.last_scraped_at == scraped_at
    assert detail.image_url == "https://example.test/large.jpg"
    assert detail.sizes == ["34", "34,5", "35"]
    assert detail.sources[0].source.slug == "bergfreunde"
    assert detail.sources[0].link.external_id == "301-0448"


def test_list_products_aggregates_offers(session: Session) -> None:
    repository = ProductRepository(session)
    product = repository.create_product(title="La Sportiva Solution")
    shop = _shop(session)
    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )
    repository.update_scraped_source(
        link,
        ScrapedProduct(
            external_id="1",
            title="Solution",
            price=Decimal("99.90"),
            currency="EUR",
            sizes=["40"],
        ),
        scraped_at=datetime(2026, 11, 12, 8, 0, tzinfo=UTC),
    )
    session.commit()

    page = repository.list_products()

    assert page.total == 1
    assert page.products[0].product.title == "La Sportiva Solution"
    assert page.products[0].source_count == 1
    assert page.products[0].lowest_price == Decimal("99.90")
    assert page.products[0].currency == "EUR"


def test_remove_product_source(session: Session) -> None:
    repository = ProductRepository(session)
    product = repository.create_product(title="La Sportiva Solution")
    shop = _shop(session)
    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )

    assert repository.remove_product_source(
        product_id=product.id or 0, link_id=link.id or 0
    )
    assert session.exec(select(ProductSource)).all() == []
    assert session.exec(select(Product)).all() != []

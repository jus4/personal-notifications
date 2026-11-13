from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ScrapedProduct
from app.scrapers.registry import PRODUCT_ADAPTERS
from app.services.products import ProductRepository


def _shop(session: Session, adapter: str = "fake-product") -> Source:
    source = Source(
        slug="bergfreunde",
        kind="products",
        adapter=adapter,
        enabled=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def test_products_page_requires_authentication(client: TestClient) -> None:
    response = client.get("/products", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_create_product_link_and_unlink(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)

    response = authenticated_client.post(
        "/products",
        data={"title": "La Sportiva Solution", "image_url": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    product_id = int(response.headers["location"].rstrip("/").split("/")[-1])

    url = "https://www.bergfreunde.fi/la-sportiva-solution-kiipeilykengaet/"
    response = authenticated_client.post(
        f"/products/{product_id}/sources",
        data={"source_id": shop.id, "url": url},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    link = session.exec(select(ProductSource)).one()
    assert link.product_id == product_id
    assert link.source_id == shop.id
    assert link.url == url

    response = authenticated_client.get(f"/products/{product_id}")
    assert response.status_code == 200
    assert "La Sportiva Solution" in response.text
    assert "bergfreunde" in response.text
    assert url in response.text
    assert "Unlink" in response.text
    assert "Link offer" in response.text

    response = authenticated_client.post(
        f"/products/{product_id}/sources/{link.id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    assert session.exec(select(ProductSource)).all() == []
    assert session.exec(select(Product)).all() != []


def test_duplicate_product_source_returns_error(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    url = "https://example.test/solution"
    repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url=url,
    )

    response = authenticated_client.post(
        f"/products/{product.id}/sources",
        data={"source_id": shop.id, "url": url},
    )

    assert response.status_code == 400
    assert "already linked" in response.text


def test_products_list_shows_offer_summary(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    product = repository.create_product(title="La Sportiva Solution")
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
        scraped_at=link.created_at,
    )
    session.commit()

    response = authenticated_client.get("/products")

    assert response.status_code == 200
    assert "La Sportiva Solution" in response.text
    assert "99.90" in response.text
    assert "bergfreunde" not in response.text
    assert "Scrape products" in response.text


def test_scrape_products_htmx_updates_links(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            return [
                ScrapedProduct(
                    external_id="301-0448",
                    title="Scraped solution",
                    price=Decimal("131.96"),
                    currency="EUR",
                    sizes=["40", "41"],
                )
            ]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)

    response = authenticated_client.post(
        "/products/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Product scrape results" in response.text
    assert "bergfreunde" in response.text

    session.expire_all()
    updated = session.get(ProductSource, link.id)
    assert updated is not None
    assert updated.title == "Scraped solution"
    assert updated.price == Decimal("131.96")
    assert updated.sizes == ["40", "41"]


def test_scrape_products_shows_link_errors(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )

    class BadAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            raise RuntimeError("blocked by shop")

    monkeypatch.setitem(PRODUCT_ADAPTERS, BadAdapter.key, BadAdapter)

    response = authenticated_client.post(
        "/products/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "Errors" in response.text
    assert "blocked by shop" in response.text

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.category import Category
from app.models.manufacturer import Manufacturer
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
    assert "Manufacturer:" in response.text
    assert "Category:" in response.text
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
    manufacturer = Manufacturer(name="La Sportiva", slug="la-sportiva")
    category = Category(name="Climbing shoes", slug="climbing-shoes")
    session.add(manufacturer)
    session.add(category)
    session.commit()
    session.refresh(manufacturer)
    session.refresh(category)
    repository = ProductRepository(session)
    product = repository.create_product(
        title="La Sportiva Solution",
        manufacturer_id=manufacturer.id,
        category_id=category.id,
    )
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
    assert "Manufacturer" in response.text
    assert "Category" in response.text
    assert "La Sportiva" in response.text
    assert "Climbing shoes" in response.text
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


def test_product_pages_render_single_scrape_controls(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    product = repository.create_product(title="Solution")
    repository.add_product_source(
        product_id=product.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )

    list_response = authenticated_client.get("/products")
    assert list_response.status_code == 200
    assert "Scrape product" in list_response.text
    assert f'hx-post="/products/{product.id}/scrape"' in list_response.text
    assert 'id="product-scrape-feedback"' in list_response.text
    assert 'hx-trigger="productScraped from:body"' in list_response.text

    detail_response = authenticated_client.get(f"/products/{product.id}")
    assert detail_response.status_code == 200
    assert "Scrape product" in detail_response.text
    assert f'hx-post="/products/{product.id}/scrape"' in detail_response.text
    assert 'id="product-scrape-feedback"' in detail_response.text
    assert 'hx-select="#product-detail-content"' in detail_response.text


def test_scrape_single_product_requires_authentication(
    client: TestClient,
) -> None:
    response = client.post("/products/1/scrape", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_scrape_single_product_unknown_returns_404(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/products/9999/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 404


def test_scrape_single_product_htmx_updates_only_that_product(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shop = _shop(session)
    repository = ProductRepository(session)
    target = repository.create_product(title="Solution")
    other = repository.create_product(title="Miura")
    target_link = repository.add_product_source(
        product_id=target.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/solution",
    )
    other_link = repository.add_product_source(
        product_id=other.id or 0,
        source_id=shop.id or 0,
        url="https://example.test/miura",
    )
    fetched: list[str] = []

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            fetched.append(url)
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
        f"/products/{target.id}/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "productScraped"
    assert "Product scrape results" in response.text
    assert "bergfreunde" in response.text
    assert fetched == [target_link.url]

    session.expire_all()
    updated = session.get(ProductSource, target_link.id)
    assert updated is not None
    assert updated.title == "Scraped solution"
    assert updated.price == Decimal("131.96")
    assert updated.sizes == ["40", "41"]
    assert updated.last_scraped_at is not None

    untouched = session.get(ProductSource, other_link.id)
    assert untouched is not None
    assert untouched.title is None
    assert untouched.last_scraped_at is None


def test_scrape_single_product_non_htmx_redirects_to_detail(
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

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            return [ScrapedProduct(external_id="1", title="Solution")]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)

    response = authenticated_client.post(
        f"/products/{product.id}/scrape",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/products/{product.id}"


def test_scrape_single_product_without_offers_shows_message(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    product = ProductRepository(session).create_product(title="Solution")

    response = authenticated_client.post(
        f"/products/{product.id}/scrape",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "No offers linked" in response.text

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ScrapedProduct
from app.scrapers.registry import PRODUCT_ADAPTERS
from app.services.products import ProductRepository


def _shop(session: Session) -> Source:
    source = Source(
        slug="bergfreunde",
        kind="products",
        adapter="fake-product",
        enabled=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _link(
    session: Session,
    source: Source,
    *,
    product_title: str,
    url: str,
    error: str | None = None,
) -> ProductSource:
    repository = ProductRepository(session)
    product = repository.create_product(title=product_title)
    link = repository.add_product_source(
        product_id=product.id or 0,
        source_id=source.id or 0,
        url=url,
    )
    if error is not None:
        link.last_error = error
        session.add(link)
        session.commit()
        session.refresh(link)
    return link


def _install_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    external_id: str = "scraped-id",
    title: str = "La Sportiva Solution",
    error: Exception | None = None,
) -> None:
    class FakeAdapter:
        key = "fake-product"

        async def fetch(
            self,
            source: Source,
            url: str,
        ) -> list[ScrapedProduct]:
            if error is not None:
                raise error
            return [
                ScrapedProduct(
                    external_id=external_id,
                    title=title,
                    price=Decimal("129.90"),
                    currency="EUR",
                )
            ]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)


def test_product_sources_page_requires_authentication(
    client: TestClient,
) -> None:
    response = client.get("/product-sources", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_product_sources_page_lists_only_errors_by_default(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    errored = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )
    healthy = _link(
        session,
        shop,
        product_title="Scarpa Instinct",
        url="https://example.test/healthy",
        error=None,
    )

    response = authenticated_client.get("/product-sources")

    assert response.status_code == 200
    assert "La Sportiva Solution" in response.text
    assert errored.url in response.text
    assert "HTTP 404" in response.text
    assert "Scarpa Instinct" not in response.text
    assert healthy.url not in response.text


def test_product_sources_page_show_all_includes_healthy_links(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )
    healthy = _link(
        session,
        shop,
        product_title="Scarpa Instinct",
        url="https://example.test/healthy",
        error=None,
    )

    response = authenticated_client.get("/product-sources?show_all=1")

    assert response.status_code == 200
    assert "La Sportiva Solution" in response.text
    assert "Scarpa Instinct" in response.text
    assert healthy.url in response.text


def test_update_product_source_url(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )
    link.external_id = "old-id"
    link.title = "Old scraped title"
    link.price = Decimal("99.90")
    link.sizes = ["40"]
    link.availability = "in_stock"
    session.add(link)
    session.commit()

    new_url = "https://example.test/fixed"
    response = authenticated_client.post(
        f"/product-sources/{link.id}/url",
        data={"url": new_url},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/product-sources")

    session.expire_all()
    updated = session.get(ProductSource, link.id)
    assert updated is not None
    assert updated.url == new_url
    assert updated.last_error is None
    assert updated.external_id == "old-id"
    assert updated.title == "Old scraped title"
    assert updated.price == Decimal("99.90")
    assert updated.sizes == ["40"]
    assert updated.availability == "in_stock"


@pytest.mark.parametrize("action", ["url", "test"])
def test_invalid_product_source_url_is_rejected(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    _install_adapter(
        monkeypatch,
        error=AssertionError("adapter should not be called"),
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )

    response = authenticated_client.post(
        f"/product-sources/{link.id}/{action}",
        data={"url": "not-a-url"},
    )

    assert response.status_code == 400
    assert "Invalid URL" in response.text

    session.expire_all()
    unchanged = session.get(ProductSource, link.id)
    assert unchanged is not None
    assert unchanged.url == "https://example.test/broken"
    assert unchanged.last_error == "HTTP 404"


def test_update_product_source_url_rejects_duplicate_url(
    authenticated_client: TestClient,
    session: Session,
) -> None:
    shop = _shop(session)
    first = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/first",
        error="HTTP 404",
    )
    duplicate = _link(
        session,
        shop,
        product_title="Scarpa Instinct",
        url="https://example.test/duplicate",
    )

    response = authenticated_client.post(
        f"/product-sources/{first.id}/url",
        data={"url": duplicate.url},
    )

    assert response.status_code == 400
    assert "already linked" in response.text

    session.expire_all()
    unchanged = session.get(ProductSource, first.id)
    assert unchanged is not None
    assert unchanged.url == "https://example.test/first"
    assert unchanged.last_error == "HTTP 404"


@pytest.mark.parametrize("action", ["url", "test"])
def test_product_source_actions_require_authentication(
    client: TestClient,
    action: str,
) -> None:
    response = client.post(
        f"/product-sources/1/{action}",
        data={"url": "https://example.test/fixed"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("action", ["url", "test"])
def test_unknown_product_source_returns_404(
    authenticated_client: TestClient,
    action: str,
) -> None:
    response = authenticated_client.post(
        f"/product-sources/999/{action}",
        data={"url": "https://example.test/fixed"},
    )

    assert response.status_code == 404


def test_test_product_source_url_matches_by_external_id(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(
        monkeypatch,
        external_id="301-0448",
        title="Different title",
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )
    link.external_id = "301-0448"
    session.add(link)
    session.commit()

    response = authenticated_client.post(
        f"/product-sources/{link.id}/test",
        data={"url": "https://example.test/fixed"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "matched via external id" in response.text.lower()


def test_test_product_source_url_matches_by_title(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(
        monkeypatch,
        external_id="other-id",
        title="LA SPORTIVA - Solution (Women's) Climbing Shoes",
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )

    response = authenticated_client.post(
        f"/product-sources/{link.id}/test",
        data={"url": "https://example.test/fixed"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "matched via title" in response.text.lower()


def test_test_product_source_url_reports_mismatch(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(
        monkeypatch,
        external_id="other-id",
        title="Scarpa Instinct",
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )

    response = authenticated_client.post(
        f"/product-sources/{link.id}/test",
        data={"url": "https://example.test/fixed"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "does not match" in response.text.lower()


def test_test_product_source_url_reports_adapter_error(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(
        monkeypatch,
        error=RuntimeError("blocked by shop"),
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )

    response = authenticated_client.post(
        f"/product-sources/{link.id}/test",
        data={"url": "https://example.test/fixed"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "blocked by shop" in response.text


def test_test_product_source_url_does_not_mutate_link(
    authenticated_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_adapter(
        monkeypatch,
        external_id="other-id",
        title="Scarpa Instinct",
    )
    shop = _shop(session)
    link = _link(
        session,
        shop,
        product_title="La Sportiva Solution",
        url="https://example.test/broken",
        error="HTTP 404",
    )
    link.title = "Old scraped title"
    link.price = Decimal("99.90")
    session.add(link)
    session.commit()

    response = authenticated_client.post(
        f"/product-sources/{link.id}/test",
        data={"url": "https://example.test/fixed"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200

    session.expire_all()
    unchanged = session.get(ProductSource, link.id)
    assert unchanged is not None
    assert unchanged.url == "https://example.test/broken"
    assert unchanged.last_error == "HTTP 404"
    assert unchanged.title == "Old scraped title"
    assert unchanged.price == Decimal("99.90")

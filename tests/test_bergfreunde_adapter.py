import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import bergfreunde
from app.scrapers.registry import PRODUCT_ADAPTERS

PRODUCT_URL = "https://www.bergfreunde.fi/la-sportiva-solution-kiipeilykengaet/"
PRODUCT_HTML = """
<!doctype html>
<html>
  <body>
    <script type="application/ld+json">
      [
        {"@type": "BreadcrumbList", "itemListElement": []},
        {
          "@context": "https://schema.org/",
          "@type": "Product",
          "sku": "301-0448",
          "image": "https://www.bfgcdn.com/example-600.jpg",
          "name": "La Sportiva - Solution - Kiipeilykengät",
          "review": [{"@type": "Review"}],
          "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.7",
            "ratingCount": "267"
          }
        }
      ]
    </script>
    <div id="details" data-sku="301-0448"></div>
    <script>
      window.oBfArticle['abc'] = {"main":{"oxid":"abc","tpriceFormatted":"164,95 €","tprice":164.95,"percent":20,"price":131.96,"largeimage":"https://www.bfgcdn.com/example-large.jpg","stockStatus":{"text":"Toimitusaika: 5-7 arkipäivää","isBuyable":true,"oxStock":0}},"variants":{"v1":{"oxid":"v1","ean":"8058428097593","price":131.96,"tprice":164.95,"priceFormatted":"131,96 €","tpriceFormatted":"164,95 €","variants":{"color":"White / Yellow","size":"34"},"stockStatus":{"text":"Toimitusaika: 5-7 arkipäivää","isBuyable":true,"oxStock":1}},"v2":{"oxid":"v2","ean":"8058428097616","price":131.96,"tprice":164.95,"priceFormatted":"131,96 €","tpriceFormatted":"164,95 €","variants":{"color":"White / Yellow","size":"35"},"stockStatus":{"text":"Tuote on valitettavasti toistaiseksi loppunut","isBuyable":false,"oxStock":false}}}};
    </script>
  </body>
</html>
"""


def _make_source(url: str = PRODUCT_URL) -> Source:
    return Source(
        slug="bergfreunde",
        kind="products",
        adapter="bergfreunde_html",
        url=url,
    )


def _profile() -> ResolvedHttpProfile:
    return ResolvedHttpProfile(
        client="curl_cffi",
        impersonate="chrome",
        user_agent=None,
        proxy_url=None,
        timeout_seconds=15.0,
        http_version=None,
    )


def _patch_fetch(
    monkeypatch: pytest.MonkeyPatch,
    html: str,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    profile = _profile()

    @asynccontextmanager
    async def fake_text_session(
        resolved: ResolvedHttpProfile,
        *,
        headers: dict[str, str] | None = None,
    ):
        seen["profile"] = resolved
        yield "session"

    async def fake_fetch_text(url: str, **kwargs: Any) -> str:
        seen["url"] = url
        seen["session"] = kwargs.get("session")
        seen["fetch_profile"] = kwargs.get("profile")
        return html

    monkeypatch.setattr(bergfreunde, "get_settings", lambda: object())
    monkeypatch.setattr(
        bergfreunde,
        "resolve_http_profile",
        lambda source, settings, *, html, **kwargs: profile,
    )
    monkeypatch.setattr(bergfreunde, "text_session", fake_text_session)
    monkeypatch.setattr(bergfreunde, "fetch_text", fake_fetch_text)
    return seen


def test_parse_maps_bergfreunde_product() -> None:
    product = bergfreunde.BergfreundeProductAdapter.parse(PRODUCT_HTML, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "301-0448"
    assert product.title == "La Sportiva - Solution - Kiipeilykengät"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("131.96")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.image_url == "https://www.bfgcdn.com/example-600.jpg"
    assert product.sizes == ["34", "35"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "variants": [
            {
                "variant_id": "v1",
                "ean": "8058428097593",
                "color": "White / Yellow",
                "size": "34",
                "price": 131.96,
                "list_price": 164.95,
                "price_formatted": "131,96 €",
                "list_price_formatted": "164,95 €",
                "availability": "in_stock",
                "stock_text": "Toimitusaika: 5-7 arkipäivää",
                "stock_quantity": 1,
            },
            {
                "variant_id": "v2",
                "ean": "8058428097616",
                "color": "White / Yellow",
                "size": "35",
                "price": 131.96,
                "list_price": 164.95,
                "price_formatted": "131,96 €",
                "list_price_formatted": "164,95 €",
                "availability": "out_of_stock",
                "stock_text": "Tuote on valitettavasti toistaiseksi loppunut",
            },
        ],
        "price": 131.96,
        "list_price": 164.95,
        "list_price_formatted": "164,95 €",
        "discount_percent": 20,
        "stock_text": "Toimitusaika: 5-7 arkipäivää",
        "stock_quantity": 0,
        "rating": {"value": "4.7", "count": "267"},
    }


def test_fetch_uses_shared_curl_cffi_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, PRODUCT_HTML)

    products = asyncio.run(
        bergfreunde.BergfreundeProductAdapter().fetch(_make_source(), "")
    )

    assert [product.external_id for product in products] == ["301-0448"]
    assert seen["url"] == PRODUCT_URL
    assert seen["session"] == "session"
    assert seen["profile"].client == "curl_cffi"
    assert seen["fetch_profile"].client == "curl_cffi"


def test_parse_supports_json_ld_only_pages() -> None:
    html = """
    <script type="application/ld+json">
      {"@type": "Product", "sku": "sku-1", "name": "Simple product"}
    </script>
    """

    product = bergfreunde.BergfreundeProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "sku-1"
    assert product.title == "Simple product"
    assert product.price is None
    assert product.currency is None
    assert product.availability is None
    assert product.sizes == []
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "variants": [],
    }


def test_parse_falls_back_to_url_slug_for_external_id() -> None:
    html = """
    <script type="application/ld+json">
      {"@type": "Product", "name": "Slug product"}
    </script>
    """

    product = bergfreunde.BergfreundeProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "la-sportiva-solution-kiipeilykengaet"


def test_parse_returns_none_without_product_identity() -> None:
    assert (
        bergfreunde.BergfreundeProductAdapter.parse("<html></html>", PRODUCT_URL)
        is None
    )


def test_fetch_raises_when_product_data_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch(monkeypatch, "<html></html>")

    with pytest.raises(ValueError, match="Could not find product data"):
        asyncio.run(
            bergfreunde.BergfreundeProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(
            bergfreunde.BergfreundeProductAdapter().fetch(_make_source(url=""), "")
        )


def test_bergfreunde_adapter_is_registered() -> None:
    assert PRODUCT_ADAPTERS["bergfreunde_html"] is bergfreunde.BergfreundeProductAdapter

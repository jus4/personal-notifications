import asyncio
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import aminoporssi
from app.scrapers.registry import PRODUCT_ADAPTERS

PRODUCT_URL = "https://www.aminoporssi.fi/products/solution"
PRODUCT_PAYLOAD: dict[str, Any] = {
    "id": 7966499569917,
    "title": "La Sportiva Solution kiipeilykengät",
    "handle": "solution",
    "vendor": "La Sportiva",
    "type": "Kiipeilykengät",
    "price": 14990,
    "price_min": 14990,
    "compare_at_price": None,
    "compare_at_price_min": 0,
    "available": True,
    "featured_image": (
        "//cdn.shopify.com/s/files/1/0531/6024/7463/products/"
        "LaSportiva_Solution_7.jpg?v=1669201384"
    ),
    "images": [
        (
            "//cdn.shopify.com/s/files/1/0531/6024/7463/products/"
            "LaSportiva_Solution_7.jpg?v=1669201384"
        )
    ],
    "options": [
        {
            "name": "Koko",
            "position": 1,
            "values": ["39.5", "40"],
        }
    ],
    "variants": [
        {
            "id": 43605548990717,
            "title": "39.5",
            "sku": "ZFCS013W00Y00395",
            "barcode": "8058428097708",
            "options": ["39.5"],
            "price": 14990,
            "compare_at_price": None,
            "available": True,
        },
        {
            "id": 43605549023485,
            "title": "40",
            "sku": "ZFCS013W00Y0040",
            "barcode": "8058428097715",
            "options": ["40"],
            "price": 15990,
            "compare_at_price": 19990,
            "available": False,
        },
    ],
}


def _make_source(url: str = PRODUCT_URL) -> Source:
    return Source(
        slug="aminoporssi",
        kind="products",
        adapter="aminoporssi_shopify",
        url=url,
    )


def _profile() -> ResolvedHttpProfile:
    return ResolvedHttpProfile(
        client="httpx",
        impersonate=None,
        user_agent="notification-service/0.1",
        proxy_url=None,
        timeout_seconds=15.0,
        http_version=None,
    )


def _patch_fetch(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    profile = _profile()

    async def fake_fetch_json(url: str, **kwargs: Any) -> Any:
        seen["url"] = url
        seen["fetch_profile"] = kwargs.get("profile")
        return payload

    def fake_resolve(
        source: Source,
        settings: Any,
        *,
        html: bool,
        **kwargs: Any,
    ) -> ResolvedHttpProfile:
        seen["html"] = html
        seen["resolve_kwargs"] = kwargs
        return profile

    monkeypatch.setattr(aminoporssi, "get_settings", lambda: object())
    monkeypatch.setattr(aminoporssi, "resolve_http_profile", fake_resolve)
    monkeypatch.setattr(aminoporssi, "fetch_json", fake_fetch_json)
    return seen


def test_parse_maps_aminoporssi_product() -> None:
    product = aminoporssi.AminoporssiProductAdapter.parse(
        PRODUCT_PAYLOAD,
        PRODUCT_URL,
    )

    assert product is not None
    assert product.external_id == "7966499569917"
    assert product.title == "La Sportiva Solution kiipeilykengät"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("149.90")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.image_url == (
        "https://cdn.shopify.com/s/files/1/0531/6024/7463/products/"
        "LaSportiva_Solution_7.jpg?v=1669201384"
    )
    assert product.sizes == ["39.5", "40"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "product_id": "7966499569917",
        "options": [{"name": "Koko", "values": ["39.5", "40"]}],
        "variants": [
            {
                "variant_id": "43605548990717",
                "title": "39.5",
                "sku": "ZFCS013W00Y00395",
                "barcode": "8058428097708",
                "options": ["39.5"],
                "price": 149.9,
                "available": True,
            },
            {
                "variant_id": "43605549023485",
                "title": "40",
                "sku": "ZFCS013W00Y0040",
                "barcode": "8058428097715",
                "options": ["40"],
                "price": 159.9,
                "compare_at_price": 199.9,
                "available": False,
            },
        ],
        "handle": "solution",
        "vendor": "La Sportiva",
        "product_type": "Kiipeilykengät",
        "price": 149.9,
    }


def test_parse_maps_out_of_stock_product() -> None:
    product = aminoporssi.AminoporssiProductAdapter.parse(
        {
            "id": 2,
            "title": "Sold out product",
            "price": 1000,
            "available": False,
        },
        PRODUCT_URL,
    )

    assert product is not None
    assert product.price == Decimal("10.00")
    assert product.availability == "out_of_stock"
    assert product.sizes == []


def test_parse_ignores_default_title_option_as_size() -> None:
    product = aminoporssi.AminoporssiProductAdapter.parse(
        {
            "id": 3,
            "title": "Capsules",
            "available": True,
            "options": [{"name": "Title", "values": ["Default Title"]}],
            "variants": [{"id": 30, "title": "Default Title"}],
        },
        PRODUCT_URL,
    )

    assert product is not None
    assert product.sizes == []


def test_parse_falls_back_to_first_image() -> None:
    product = aminoporssi.AminoporssiProductAdapter.parse(
        {
            "id": 4,
            "title": "Image fallback",
            "featured_image": None,
            "images": ["//cdn.shopify.com/first.jpg", "//cdn.shopify.com/second.jpg"],
        },
        PRODUCT_URL,
    )

    assert product is not None
    assert product.image_url == "https://cdn.shopify.com/first.jpg"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "Missing id"},
        {"id": 1},
        "not a product object",
    ],
)
def test_parse_returns_none_without_product_identity(payload: Any) -> None:
    assert (
        aminoporssi.AminoporssiProductAdapter.parse(payload, PRODUCT_URL) is None
    )


def test_product_json_url_appends_js_suffix() -> None:
    assert aminoporssi._product_json_url(PRODUCT_URL) == f"{PRODUCT_URL}.js"
    assert aminoporssi._product_json_url(f"{PRODUCT_URL}/") == f"{PRODUCT_URL}.js"
    assert (
        aminoporssi._product_json_url(f"{PRODUCT_URL}?variant=123")
        == f"{PRODUCT_URL}.js?variant=123"
    )
    assert aminoporssi._product_json_url(f"{PRODUCT_URL}.js") == f"{PRODUCT_URL}.js"


def test_fetch_uses_httpx_json_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, PRODUCT_PAYLOAD)

    products = asyncio.run(
        aminoporssi.AminoporssiProductAdapter().fetch(_make_source(), "")
    )

    assert [product.external_id for product in products] == ["7966499569917"]
    assert seen["url"] == f"{PRODUCT_URL}.js"
    assert seen["fetch_profile"].client == "httpx"
    assert seen["html"] is False
    assert seen["resolve_kwargs"]["allow_default_proxy"] is False


def test_fetch_preserves_variant_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, PRODUCT_PAYLOAD)

    asyncio.run(
        aminoporssi.AminoporssiProductAdapter().fetch(
            _make_source(f"{PRODUCT_URL}?variant=123"),
            "",
        )
    )

    assert seen["url"] == f"{PRODUCT_URL}.js?variant=123"


def test_fetch_raises_when_product_data_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch(monkeypatch, {"title": "No id"})

    with pytest.raises(ValueError, match="Could not find product data"):
        asyncio.run(
            aminoporssi.AminoporssiProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(
            aminoporssi.AminoporssiProductAdapter().fetch(_make_source(url=""), "")
        )


def test_aminoporssi_adapter_is_registered() -> None:
    assert (
        PRODUCT_ADAPTERS["aminoporssi_shopify"]
        is aminoporssi.AminoporssiProductAdapter
    )

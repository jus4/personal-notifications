import asyncio
import copy
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import epictv
from app.scrapers.registry import PRODUCT_ADAPTERS

PRODUCT_URL = "https://epictv.com/la-sportiva-solution-climbing-shoes"
PRODUCT_IMAGE = "https://epictv.com/media/catalog/product/2/0/20g_000100_1.jpg"
PRODUCT_GROUP: dict[str, Any] = {
    "@context": "https://schema.org/",
    "@type": "ProductGroup",
    "name": "La Sportiva Solution climbing shoes",
    "url": PRODUCT_URL,
    "productGroupID": "LASPORTIVA20G000100",
    "brand": {"@type": "Brand", "name": "La Sportiva"},
    "image": PRODUCT_IMAGE,
    "variesBy": ["https://schema.org/size"],
    "hasVariant": [
        {
            "@type": "Product",
            "name": "La Sportiva Solution climbing shoes - 40",
            "sku": "LASPORTIVAZFCS013W00Y00-40",
            "gtin": "8058428097708",
            "size": "40",
            "image": (
                "https://epictv.com/media/catalog/product/2/0/20g_000100_40.jpg"
            ),
            "offers": {
                "@type": "Offer",
                "url": (
                    "https://epictv.com/la-sportiva-solution-climbing-shoes"
                    "?taglia_scapette=40"
                ),
                "priceCurrency": "USD",
                "price": 125.74,
                "availability": "https://schema.org/InStock",
            },
        },
        {
            "@type": "Product",
            "name": "La Sportiva Solution climbing shoes - 41",
            "sku": "LASPORTIVAZFCS013W00Y00-41",
            "gtin": "8058428097715",
            "size": "41",
            "image": (
                "https://epictv.com/media/catalog/product/2/0/20g_000100_41.jpg"
            ),
            "offers": {
                "@type": "Offer",
                "url": (
                    "https://epictv.com/la-sportiva-solution-climbing-shoes"
                    "?taglia_scapette=41"
                ),
                "priceCurrency": "USD",
                "price": 129.99,
                "availability": "https://schema.org/OutOfStock",
            },
        },
    ],
}


def _html(*payloads: Any) -> str:
    scripts = "\n".join(
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        for payload in payloads
    )
    return f"<html><head>{scripts}</head><body></body></html>"


def _make_source(url: str = PRODUCT_URL) -> Source:
    return Source(
        slug="epictv",
        kind="products",
        adapter="epictv_html",
        url=url,
    )


def _profile() -> ResolvedHttpProfile:
    return ResolvedHttpProfile(
        client="curl_cffi",
        impersonate="chrome",
        user_agent=None,
        proxy_url=None,
        timeout_seconds=30.0,
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

    monkeypatch.setattr(epictv, "get_settings", lambda: object())
    monkeypatch.setattr(epictv, "resolve_http_profile", fake_resolve)
    monkeypatch.setattr(epictv, "text_session", fake_text_session)
    monkeypatch.setattr(epictv, "fetch_text", fake_fetch_text)
    return seen


def test_parse_maps_epictv_product() -> None:
    product = epictv.EpictvProductAdapter.parse(
        _html([{"@type": "OnlineStore"}, PRODUCT_GROUP]),
        PRODUCT_URL,
    )

    assert product is not None
    assert product.external_id == "LASPORTIVA20G000100"
    assert product.title == "La Sportiva Solution climbing shoes"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("125.74")
    assert product.currency == "USD"
    assert product.availability == "in_stock"
    assert product.image_url == PRODUCT_IMAGE
    assert product.sizes == ["40", "41"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "USD",
        "product_group_id": "LASPORTIVA20G000100",
        "variants": [
            {
                "size": "40",
                "sku": "LASPORTIVAZFCS013W00Y00-40",
                "gtin": "8058428097708",
                "price": 125.74,
                "currency": "USD",
                "availability": "in_stock",
                "image": (
                    "https://epictv.com/media/catalog/product/2/0/20g_000100_40.jpg"
                ),
                "offer_url": (
                    "https://epictv.com/la-sportiva-solution-climbing-shoes"
                    "?taglia_scapette=40"
                ),
            },
            {
                "size": "41",
                "sku": "LASPORTIVAZFCS013W00Y00-41",
                "gtin": "8058428097715",
                "price": 129.99,
                "currency": "USD",
                "availability": "out_of_stock",
                "image": (
                    "https://epictv.com/media/catalog/product/2/0/20g_000100_41.jpg"
                ),
                "offer_url": (
                    "https://epictv.com/la-sportiva-solution-climbing-shoes"
                    "?taglia_scapette=41"
                ),
            },
        ],
        "brand": "La Sportiva",
        "price": 125.74,
    }


def test_parse_marks_product_out_of_stock_when_all_variants_are_out() -> None:
    group = copy.deepcopy(PRODUCT_GROUP)
    for variant in group["hasVariant"]:
        variant["offers"]["availability"] = "https://schema.org/OutOfStock"

    product = epictv.EpictvProductAdapter.parse(_html(group), PRODUCT_URL)

    assert product is not None
    assert product.availability == "out_of_stock"


def test_parse_supports_single_product_and_offer_list() -> None:
    payload = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": "Simple product",
        "sku": "SKU-1",
        "size": "42",
        "image": "https://epictv.com/media/catalog/product/simple.jpg",
        "offers": [
            {
                "@type": "Offer",
                "url": f"{PRODUCT_URL}?taglia_scapette=42",
                "price": "10,50",
                "priceCurrency": "eur",
                "availability": "https://schema.org/InStock",
            }
        ],
    }

    product = epictv.EpictvProductAdapter.parse(_html(payload), PRODUCT_URL)

    assert product is not None
    assert product.external_id == "SKU-1"
    assert product.price == Decimal("10.50")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.sizes == ["42"]


def test_parse_skips_malformed_json_ld() -> None:
    html = (
        '<script type="application/ld+json">not json</script>'
        + _html(PRODUCT_GROUP)
    )

    product = epictv.EpictvProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "LASPORTIVA20G000100"


def test_parse_falls_back_to_url_slug_for_external_id() -> None:
    payload = {
        "@type": "ProductGroup",
        "name": "Slug product",
        "hasVariant": [],
    }

    product = epictv.EpictvProductAdapter.parse(_html(payload), PRODUCT_URL)

    assert product is not None
    assert product.external_id == "la-sportiva-solution-climbing-shoes"


def test_parse_returns_none_without_product_identity() -> None:
    assert epictv.EpictvProductAdapter.parse("<html></html>", PRODUCT_URL) is None
    assert (
        epictv.EpictvProductAdapter.parse(
            _html({"@type": "ProductGroup", "name": "No id"}),
            "https://epictv.com/",
        )
        is None
    )
    assert (
        epictv.EpictvProductAdapter.parse(
            _html({"@type": "ProductGroup", "productGroupID": "123"}),
            PRODUCT_URL,
        )
        is None
    )


def test_fetch_uses_shared_curl_cffi_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, _html(PRODUCT_GROUP))

    products = asyncio.run(epictv.EpictvProductAdapter().fetch(_make_source(), ""))

    assert [product.external_id for product in products] == [
        "LASPORTIVA20G000100"
    ]
    assert seen["url"] == PRODUCT_URL
    assert seen["session"] == "session"
    assert seen["profile"].client == "curl_cffi"
    assert seen["profile"].impersonate == "chrome"
    assert seen["fetch_profile"].client == "curl_cffi"
    assert seen["fetch_profile"].impersonate == "chrome"
    assert seen["html"] is True
    assert seen["resolve_kwargs"]["allow_default_proxy"] is False


def test_fetch_raises_when_product_data_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch(monkeypatch, "<html></html>")

    with pytest.raises(ValueError, match="Could not find product data"):
        asyncio.run(
            epictv.EpictvProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(
            epictv.EpictvProductAdapter().fetch(_make_source(url=""), "")
        )


def test_epictv_adapter_is_registered() -> None:
    assert PRODUCT_ADAPTERS["epictv_html"] is epictv.EpictvProductAdapter

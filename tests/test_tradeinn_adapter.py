import asyncio
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import tradeinn
from app.scrapers.registry import PRODUCT_ADAPTERS

SAMPLE_URL = (
    "https://www.tradeinn.com/trekkinn/fi/"
    "la-sportiva-solution-kiipeilykengat/136688721/"
)
PRODUCT_URL = (
    "https://www.tradeinn.com/trekkinn/fi/"
    "la-sportiva-solution-kiipeilykengat/136688721/p"
)
PRODUCT_IMAGE = (
    "https://www.tradeinn.com/f/13668/136688721/"
    "la-sportiva-solution-kiipeilykengat.webp"
)
PRODUCT_GROUP: dict[str, Any] = {
    "@context": "https://schema.org",
    "@type": "WebPage",
    "mainEntity": {
        "@type": "ProductGroup",
        "name": "La sportiva Solution Kiipeilykengät",
        "sku": "136688721",
        "url": PRODUCT_URL,
        "image": PRODUCT_IMAGE,
        "mpn": "20G000100.38",
        "brand": {"@type": "Brand", "name": "La sportiva"},
        "category": "Jalkineet Kiipeilykengät",
        "aggregateRating": {
            "@type": "AggregateRating",
            "reviewCount": "73",
            "ratingValue": "5",
        },
        "hasVariant": [
            {
                "@type": "Product",
                "name": "La sportiva Solution Kiipeilykengät Valkoinen",
                "color": "Valkoinen",
                "size": "EU 36",
                "gtin13": "8020647650736",
                "productGroupID": "136688721",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650736.webp"
                ),
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/InStock",
                    "price": "136.99",
                },
            },
            {
                "@type": "Product",
                "name": "La sportiva Solution Kiipeilykengät Valkoinen",
                "color": "Valkoinen",
                "size": "EU 36 1/2",
                "gtin13": "8020647650743",
                "productGroupID": "136688721",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650743.webp"
                ),
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/InStock",
                    "price": "144.49",
                },
            },
            {
                "@type": "Product",
                "name": "La sportiva Solution Kiipeilykengät Valkoinen",
                "color": "Valkoinen",
                "size": "EU 37 1/2",
                "gtin13": "8020647650767",
                "productGroupID": "136688721",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650767.webp"
                ),
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/OutOfStock",
                    "price": "144.49",
                },
            },
            {
                "@type": "Product",
                "name": "La sportiva Solution Kiipeilykengät Valkoinen",
                "color": "Valkoinen",
                "size": "EU 38",
                "gtin13": "8020647650774",
                "productGroupID": "136688721",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650774.webp"
                ),
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/InStock",
                    "price": "136.99",
                },
            },
        ],
    },
}


def _html(*payloads: Any) -> str:
    scripts = "\n".join(
        f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        for payload in payloads
    )
    return f"<html><head>{scripts}</head><body></body></html>"


def _make_source(url: str = SAMPLE_URL) -> Source:
    return Source(
        slug="tradeinn",
        kind="products",
        adapter="tradeinn_html",
        url=url,
    )


def _profile() -> ResolvedHttpProfile:
    return ResolvedHttpProfile(
        client="curl_cffi",
        impersonate=None,
        user_agent="curl/8.21.0",
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

    monkeypatch.setattr(tradeinn, "get_settings", lambda: object())
    monkeypatch.setattr(tradeinn, "resolve_http_profile", fake_resolve)
    monkeypatch.setattr(tradeinn, "text_session", fake_text_session)
    monkeypatch.setattr(tradeinn, "fetch_text", fake_fetch_text)
    return seen


def test_parse_maps_tradeinn_product() -> None:
    product = tradeinn.TradeinnProductAdapter.parse(
        _html(PRODUCT_GROUP),
        PRODUCT_URL,
    )

    assert product is not None
    assert product.external_id == "136688721"
    assert product.title == "La sportiva Solution Kiipeilykengät"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("136.99")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.image_url == PRODUCT_IMAGE
    assert product.sizes == ["EU 36", "EU 36 1/2", "EU 37 1/2", "EU 38"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "product_id": "136688721",
        "variants": [
            {
                "size": "EU 36",
                "color": "Valkoinen",
                "gtin13": "8020647650736",
                "price": 136.99,
                "currency": "EUR",
                "availability": "in_stock",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650736.webp"
                ),
            },
            {
                "size": "EU 36 1/2",
                "color": "Valkoinen",
                "gtin13": "8020647650743",
                "price": 144.49,
                "currency": "EUR",
                "availability": "in_stock",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650743.webp"
                ),
            },
            {
                "size": "EU 37 1/2",
                "color": "Valkoinen",
                "gtin13": "8020647650767",
                "price": 144.49,
                "currency": "EUR",
                "availability": "out_of_stock",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650767.webp"
                ),
            },
            {
                "size": "EU 38",
                "color": "Valkoinen",
                "gtin13": "8020647650774",
                "price": 136.99,
                "currency": "EUR",
                "availability": "in_stock",
                "image": (
                    "https://www.tradeinn.com/f/13668/136688721/"
                    "8020647650774.webp"
                ),
            },
        ],
        "mpn": "20G000100.38",
        "brand": "La sportiva",
        "category": "Jalkineet Kiipeilykengät",
        "rating": {"value": "5", "count": "73"},
        "price": 136.99,
    }


def test_parse_supports_single_product_and_offer_list() -> None:
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Simple product",
        "sku": "999",
        "image": "https://www.tradeinn.com/f/999.webp",
        "size": "M",
        "offers": [
            {
                "@type": "Offer",
                "price": "10,50",
                "priceCurrency": "usd",
                "availability": "https://schema.org/InStock",
            }
        ],
    }

    product = tradeinn.TradeinnProductAdapter.parse(
        _html(payload),
        "https://www.tradeinn.com/trekkinn/fi/simple/999/p",
    )

    assert product is not None
    assert product.external_id == "999"
    assert product.price == Decimal("10.50")
    assert product.currency == "USD"
    assert product.availability == "in_stock"
    assert product.sizes == ["M"]


def test_parse_marks_product_out_of_stock_when_all_variants_are_out() -> None:
    payload = {
        "@type": "ProductGroup",
        "name": "Sold out product",
        "sku": "123",
        "hasVariant": [
            {
                "@type": "Product",
                "size": "40",
                "offers": {
                    "price": "99.00",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/OutOfStock",
                },
            },
            {
                "@type": "Product",
                "size": "41",
                "offers": {
                    "price": "99.00",
                    "priceCurrency": "EUR",
                    "availability": "https://schema.org/SoldOut",
                },
            },
        ],
    }

    product = tradeinn.TradeinnProductAdapter.parse(
        _html(payload),
        "https://www.tradeinn.com/trekkinn/fi/sold-out/123/p",
    )

    assert product is not None
    assert product.availability == "out_of_stock"


def test_parse_skips_malformed_json_ld() -> None:
    html = (
        '<script type="application/ld+json">not json</script>'
        + _html(PRODUCT_GROUP)
    )

    product = tradeinn.TradeinnProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "136688721"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (SAMPLE_URL, PRODUCT_URL),
        (SAMPLE_URL.rstrip("/"), PRODUCT_URL),
        (PRODUCT_URL, PRODUCT_URL),
        (f"{SAMPLE_URL}?variant=123", f"{PRODUCT_URL}?variant=123"),
    ],
)
def test_normalize_product_url(url: str, expected: str) -> None:
    assert tradeinn._normalize_product_url(url) == expected


def test_parse_returns_none_without_product_identity() -> None:
    assert tradeinn.TradeinnProductAdapter.parse("<html></html>", PRODUCT_URL) is None
    assert (
        tradeinn.TradeinnProductAdapter.parse(
            _html({"@type": "Product", "name": "No sku"}),
            PRODUCT_URL,
        )
        is None
    )


def test_fetch_uses_shared_curl_cffi_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, _html(PRODUCT_GROUP))

    products = asyncio.run(
        tradeinn.TradeinnProductAdapter().fetch(_make_source(), "")
    )

    assert [product.external_id for product in products] == ["136688721"]
    assert products[0].url == PRODUCT_URL
    assert seen["url"] == PRODUCT_URL
    assert seen["session"] == "session"
    assert seen["profile"].client == "curl_cffi"
    assert seen["fetch_profile"].client == "curl_cffi"
    assert seen["html"] is True
    assert seen["resolve_kwargs"]["allow_default_proxy"] is False


def test_fetch_raises_when_product_data_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_fetch(monkeypatch, "<html></html>")

    with pytest.raises(ValueError, match="Could not find product data"):
        asyncio.run(
            tradeinn.TradeinnProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(tradeinn.TradeinnProductAdapter().fetch(_make_source(url=""), ""))


def test_tradeinn_adapter_is_registered() -> None:
    assert PRODUCT_ADAPTERS["tradeinn_html"] is tradeinn.TradeinnProductAdapter

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import hardloop
from app.scrapers.registry import PRODUCT_ADAPTERS

PRODUCT_URL = (
    "https://www.hardloop.fi/tuotteen/"
    "75320-la-sportiva-solution-kiipeilykengaet-miehet"
)
PRODUCT_IMAGE = (
    "https://images.hardloop.fr/810597-large_default/"
    "la-sportiva-solution-kiipeilykengaet-miehet.jpg"
)
NEXT_DATA: dict[str, Any] = {
    "props": {
        "pageProps": {
            "fallback": {
                "https://api-shop.hardloop.fr/api/v2/products/75320": {
                    "id_product": 75320,
                    "name": "Solution - Kiipeilykengät - Miehet",
                    "name_h1": (
                        "La Sportiva Solution - Kiipeilykengät - Miehet"
                    ),
                    "manufacturer_name": "La Sportiva",
                },
                (
                    "https://api-shop.hardloop.fr/api/v2/products/75320/"
                    "color-references"
                ): {
                    "general_default_id_product_attribute": 101,
                    "color_references": [
                        {
                            "color": "White / Yellow",
                            "default_id_product_attribute": 101,
                            "variations": [
                                {
                                    "id_product_attribute": 101,
                                    "name": "40",
                                    "quantity": 3,
                                    "out_of_stock": 1,
                                    "out_of_stock_forced": None,
                                    "reference": "ZFCS013W00Y00-40",
                                    "prices": {
                                        "price": "129.91",
                                        "old_price": "164.90",
                                    },
                                    "images": [
                                        {
                                            "large_default": PRODUCT_IMAGE,
                                        }
                                    ],
                                },
                                {
                                    "id_product_attribute": 102,
                                    "name": "41",
                                    "quantity": 0,
                                    "out_of_stock": 1,
                                    "out_of_stock_forced": None,
                                    "reference": "ZFCS013W00Y00-41",
                                    "prices": {
                                        "price": "139.91",
                                        "old_price": "164.90",
                                    },
                                    "images": [
                                        {
                                            "large_default": (
                                                "https://images.hardloop.fr/"
                                                "810598-large_default/"
                                                "la-sportiva-solution-"
                                                "kiipeilykengaet-miehet.jpg"
                                            ),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                },
            }
        }
    }
}


def _html(next_data: dict[str, Any] | None = None) -> str:
    script = ""
    if next_data is not None:
        script = (
            '<script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(next_data)}"
            "</script>"
        )
    return (
        "<html><head>"
        '<meta property="og:image" content="https://images.hardloop.fr/og.jpg">'
        f"</head><body>{script}</body></html>"
    )


def _make_source(url: str = PRODUCT_URL) -> Source:
    return Source(
        slug="hardloop",
        kind="products",
        adapter="hardloop_html",
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

    monkeypatch.setattr(hardloop, "get_settings", lambda: object())
    monkeypatch.setattr(hardloop, "resolve_http_profile", fake_resolve)
    monkeypatch.setattr(hardloop, "text_session", fake_text_session)
    monkeypatch.setattr(hardloop, "fetch_text", fake_fetch_text)
    return seen


def test_parse_maps_hardloop_product() -> None:
    product = hardloop.HardloopProductAdapter.parse(_html(NEXT_DATA), PRODUCT_URL)

    assert product is not None
    assert product.external_id == "75320"
    assert product.title == "La Sportiva Solution - Kiipeilykengät - Miehet"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("129.91")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.image_url == PRODUCT_IMAGE
    assert product.sizes == ["40", "41"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "product_id": "75320",
        "variants": [
            {
                "color": "White / Yellow",
                "variant_id": "101",
                "size": "40",
                "quantity": 3,
                "availability": "in_stock",
                "price": 129.91,
                "old_price": 164.9,
                "reference": "ZFCS013W00Y00-40",
            },
            {
                "color": "White / Yellow",
                "variant_id": "102",
                "size": "41",
                "quantity": 0,
                "availability": "out_of_stock",
                "price": 139.91,
                "old_price": 164.9,
                "reference": "ZFCS013W00Y00-41",
            },
        ],
        "manufacturer": "La Sportiva",
        "price": 129.91,
        "list_price": 164.9,
    }


def test_parse_marks_product_out_of_stock_when_all_quantities_are_zero() -> None:
    next_data = copy.deepcopy(NEXT_DATA)
    color_refs = next_data["props"]["pageProps"]["fallback"][
        "https://api-shop.hardloop.fr/api/v2/products/75320/color-references"
    ]
    for reference in color_refs["color_references"]:
        for variation in reference["variations"]:
            variation["quantity"] = 0

    product = hardloop.HardloopProductAdapter.parse(
        _html(next_data),
        PRODUCT_URL,
    )

    assert product is not None
    assert product.availability == "out_of_stock"


def test_parse_supports_product_without_color_references() -> None:
    next_data = copy.deepcopy(NEXT_DATA)
    fallback = next_data["props"]["pageProps"]["fallback"]
    fallback.pop(
        "https://api-shop.hardloop.fr/api/v2/products/75320/color-references"
    )

    product = hardloop.HardloopProductAdapter.parse(
        _html(next_data),
        PRODUCT_URL,
    )

    assert product is not None
    assert product.external_id == "75320"
    assert product.price is None
    assert product.currency is None
    assert product.availability is None
    assert product.sizes == []
    assert product.image_url == "https://images.hardloop.fr/og.jpg"


def test_parse_falls_back_to_og_image() -> None:
    next_data = copy.deepcopy(NEXT_DATA)
    color_refs = next_data["props"]["pageProps"]["fallback"][
        "https://api-shop.hardloop.fr/api/v2/products/75320/color-references"
    ]
    for reference in color_refs["color_references"]:
        for variation in reference["variations"]:
            variation.pop("images", None)

    product = hardloop.HardloopProductAdapter.parse(
        _html(next_data),
        PRODUCT_URL,
    )

    assert product is not None
    assert product.image_url == "https://images.hardloop.fr/og.jpg"


def test_parse_returns_none_without_next_data() -> None:
    assert hardloop.HardloopProductAdapter.parse("<html></html>", PRODUCT_URL) is None


def test_parse_returns_none_with_malformed_next_data() -> None:
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        "not json"
        "</script>"
    )

    assert hardloop.HardloopProductAdapter.parse(html, PRODUCT_URL) is None


def test_parse_returns_none_without_product_payload() -> None:
    next_data = {
        "props": {
            "pageProps": {
                "fallback": {
                    "https://example.test/other": {"id": 1},
                }
            }
        }
    }

    assert (
        hardloop.HardloopProductAdapter.parse(_html(next_data), PRODUCT_URL) is None
    )


def test_fetch_uses_shared_curl_cffi_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, _html(NEXT_DATA))

    products = asyncio.run(
        hardloop.HardloopProductAdapter().fetch(_make_source(), "")
    )

    assert [product.external_id for product in products] == ["75320"]
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
            hardloop.HardloopProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(
            hardloop.HardloopProductAdapter().fetch(_make_source(url=""), "")
        )


def test_hardloop_adapter_is_registered() -> None:
    assert PRODUCT_ADAPTERS["hardloop_html"] is hardloop.HardloopProductAdapter

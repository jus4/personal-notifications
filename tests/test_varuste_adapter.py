import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import pytest

from app.models.source import Source
from app.scrapers.config import ResolvedHttpProfile
from app.scrapers.products import varuste
from app.scrapers.registry import PRODUCT_ADAPTERS

PRODUCT_URL = "https://varuste.net/p85496/la-sportiva-solution"
PRODUCT_HTML = """
<!doctype html>
<html>
  <head>
    <meta itemprop="price" content="139.90">
    <meta itemprop="priceCurrency" content="EUR">
    <link itemprop="availability" href="https://schema.org/InStock">
  </head>
  <body>
    <h1 itemprop="name">La Sportiva Solution</h1>
    <span itemprop="brand" itemscope itemtype="https://schema.org/Brand">
      La Sportiva
    </span>
    <img
      itemprop="image"
      src="/tiedostot/1/kuva/tuote/600/12884626.jpg"
      alt="La Sportiva Solution"
    >
    <form id="lomake" method="post">
      <input name="_tuoteid" type="hidden" value="85496">

      <div class="property_selection">
        <div class="property_type_name"><strong>Väri</strong></div>
        <input id="cart_box1_omin_property_type_1" name="_omin[]" type="hidden"
               value="145576">
        <div class="select_option selected" data-option-key="145576">
          <div class="property_value_row">
            <div class="property_value_details">
              <div id="property_name_color">White / Yellow<br>
                <small class="property_price_info_145576"></small>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="property_selection">
        <div class="property_type_name"><strong>Koko</strong></div>
        <input id="cart_box1_omin_property_type_2" name="_omin[]" type="hidden">
        <div class="ddownlist">
          <div class="select_option ddownlistitem selected"
               data-value="-">(valitse)</div>
          <div class="select_option ddownlistitem" data-value="35985">
            <div class="property_value_row">
              <div class="property_value_details">
                <div id="property_name_35985">34<br>
                  <small class="property_price_info_35985"></small>
                </div>
              </div>
            </div>
          </div>
          <div class="select_option ddownlistitem" data-value="38923">
            <div class="property_value_row">
              <div class="property_value_details">
                <div id="property_name_38923">34.5<br>
                  <small class="property_price_info_38923"></small>
                </div>
              </div>
            </div>
          </div>
          <div class="select_option ddownlistitem" data-value="38406">
            <div class="property_value_row">
              <div class="property_value_details">
                <div id="property_name_38406">35<br>
                  <small class="property_price_info_38406"></small>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="addon_product_info">
        <div class="property_selection">
          <div class="property_type_name"><strong>Koko</strong></div>
          <div class="select_option ddownlistitem" data-value="999">99</div>
        </div>
      </div>
    </form>
  </body>
</html>
"""


def _make_source(url: str = PRODUCT_URL) -> Source:
    return Source(
        slug="varuste",
        kind="products",
        adapter="varuste_html",
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

    monkeypatch.setattr(varuste, "get_settings", lambda: object())
    monkeypatch.setattr(varuste, "resolve_http_profile", fake_resolve)
    monkeypatch.setattr(varuste, "text_session", fake_text_session)
    monkeypatch.setattr(varuste, "fetch_text", fake_fetch_text)
    return seen


def test_parse_maps_varuste_product() -> None:
    product = varuste.VarusteProductAdapter.parse(PRODUCT_HTML, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "85496"
    assert product.title == "La Sportiva Solution"
    assert product.url == PRODUCT_URL
    assert product.price == Decimal("139.90")
    assert product.currency == "EUR"
    assert product.availability == "in_stock"
    assert product.image_url == (
        "https://varuste.net/tiedostot/1/kuva/tuote/600/12884626.jpg"
    )
    assert product.sizes == ["34", "34.5", "35"]
    assert product.raw_data == {
        "link": PRODUCT_URL,
        "currency": "EUR",
        "product_id": "85496",
        "variants": [
            {"size": "34", "property_value_id": "35985"},
            {"size": "34.5", "property_value_id": "38923"},
            {"size": "35", "property_value_id": "38406"},
        ],
        "brand": "La Sportiva",
        "color": "White / Yellow",
        "color_id": "145576",
        "price": 139.9,
    }


def test_parse_maps_out_of_stock_product() -> None:
    html = PRODUCT_HTML.replace(
        "https://schema.org/InStock",
        "https://schema.org/OutOfStock",
    )

    product = varuste.VarusteProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.availability == "out_of_stock"


def test_parse_falls_back_to_price_text() -> None:
    html = """
    <h1 itemprop="name">La Sportiva Solution</h1>
    <input name="_tuoteid" value="85496">
    <span id="price_text">139,90</span>
    """

    product = varuste.VarusteProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.price == Decimal("139.90")
    assert product.currency == "EUR"


def test_parse_without_size_options_returns_empty_sizes() -> None:
    html = """
    <h1 itemprop="name">Simple product</h1>
    <input name="_tuoteid" value="1">
    <form id="lomake"></form>
    """

    product = varuste.VarusteProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.sizes == []


def test_parse_falls_back_to_url_product_id() -> None:
    html = '<h1 itemprop="name">La Sportiva Solution</h1>'

    product = varuste.VarusteProductAdapter.parse(html, PRODUCT_URL)

    assert product is not None
    assert product.external_id == "85496"


def test_parse_returns_none_without_product_identity() -> None:
    assert varuste.VarusteProductAdapter.parse("<html></html>", PRODUCT_URL) is None
    assert (
        varuste.VarusteProductAdapter.parse(
            '<input name="_tuoteid" value="85496">',
            PRODUCT_URL,
        )
        is None
    )


def test_fetch_uses_shared_curl_cffi_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch_fetch(monkeypatch, PRODUCT_HTML)

    products = asyncio.run(varuste.VarusteProductAdapter().fetch(_make_source(), ""))

    assert [product.external_id for product in products] == ["85496"]
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
            varuste.VarusteProductAdapter().fetch(_make_source(), PRODUCT_URL)
        )


def test_fetch_requires_a_url() -> None:
    with pytest.raises(ValueError, match="no URL configured"):
        asyncio.run(varuste.VarusteProductAdapter().fetch(_make_source(url=""), ""))


def test_varuste_adapter_is_registered() -> None:
    assert PRODUCT_ADAPTERS["varuste_html"] is varuste.VarusteProductAdapter

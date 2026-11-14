import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.models.source import Source
from app.scrapers.base import (
    ProductAvailability,
    ProductSourceAdapter,
    ScrapedProduct,
)
from app.scrapers.config import resolve_http_profile
from app.scrapers.http import fetch_text, text_session, validate_url
from app.scrapers.registry import register_product_adapter

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "EUR"
PRODUCT_KEY_RE = re.compile(r"/products/(\d+)$")
COLOR_REFERENCES_KEY_RE = re.compile(r"/products/(\d+)/color-references$")


@register_product_adapter
class HardloopProductAdapter(ProductSourceAdapter):
    key = "hardloop_html"

    async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
        target = url or source.url
        if not target:
            raise ValueError(f"Source {source.slug!r} has no URL configured")

        validate_url(target)
        profile = resolve_http_profile(
            source,
            get_settings(),
            html=True,
            allow_default_proxy=False,
        )

        async with text_session(profile) as session:
            html = await fetch_text(target, session=session, profile=profile)

        product = self.parse(html, target)
        if product is None:
            raise ValueError(
                f"Could not find product data for {target!r} (source {source.slug!r})"
            )
        return [product]

    @classmethod
    def parse(cls, html: str, url: str) -> ScrapedProduct | None:
        soup = BeautifulSoup(html, "html.parser")
        next_data = _next_data(soup)
        if next_data is None:
            logger.warning("Could not find __NEXT_DATA__ in %s", url)
            return None

        fallback = _fallback(next_data)
        if not isinstance(fallback, dict):
            logger.warning("Could not find fallback product data in %s", url)
            return None

        product_id, product, color_refs = _product_payloads(fallback)
        if not isinstance(product, dict):
            logger.warning("Could not find product payload in %s", url)
            return None

        external_id = _as_str(product.get("id_product")) or product_id
        title = _as_str(product.get("name_h1")) or _as_str(product.get("name"))
        if not external_id or not title:
            logger.warning("Could not extract product identity from %s", url)
            return None

        variations = _variations(color_refs)
        price = _lowest_price(variations)
        list_price = _first_old_price(variations)
        currency = DEFAULT_CURRENCY if price is not None else None

        return ScrapedProduct(
            external_id=external_id,
            title=title,
            url=url,
            price=price,
            currency=currency,
            availability=_availability(variations),
            image_url=_image(color_refs, soup, url),
            sizes=_sizes(variations),
            raw_data=cls._raw_data(
                url=url,
                product=product,
                external_id=external_id,
                variations=variations,
                price=price,
                list_price=list_price,
                currency=currency,
            ),
        )

    @staticmethod
    def _raw_data(
        *,
        url: str,
        product: dict[str, Any],
        external_id: str,
        variations: list[dict[str, Any]],
        price: Decimal | None,
        list_price: Decimal | None,
        currency: str | None,
    ) -> dict[str, Any]:
        raw_data: dict[str, Any] = {
            "link": url,
            "currency": currency or DEFAULT_CURRENCY,
            "product_id": external_id,
            "variants": _variant_data(variations),
        }

        manufacturer = _as_str(product.get("manufacturer_name"))
        if manufacturer is not None:
            raw_data["manufacturer"] = manufacturer
        if price is not None:
            raw_data["price"] = float(price)
        if list_price is not None:
            raw_data["list_price"] = float(list_price)

        return raw_data


def _next_data(soup: BeautifulSoup) -> dict[str, Any] | None:
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return None
    raw = script.string if script.string is not None else script.get_text()
    raw = raw.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Skipping malformed __NEXT_DATA__ payload")
        return None
    return payload if isinstance(payload, dict) else None


def _fallback(next_data: dict[str, Any]) -> Any:
    props = next_data.get("props")
    if not isinstance(props, dict):
        return None
    page_props = props.get("pageProps")
    if not isinstance(page_props, dict):
        return None
    return page_props.get("fallback")


def _product_payloads(
    fallback: dict[str, Any],
) -> tuple[str | None, Any, Any]:
    product_id: str | None = None
    product: Any = None
    color_refs: Any = None

    for key, value in fallback.items():
        if not isinstance(key, str):
            continue
        color_match = COLOR_REFERENCES_KEY_RE.search(key)
        if color_match is not None:
            product_id = product_id or color_match.group(1)
            color_refs = value
            continue
        product_match = PRODUCT_KEY_RE.search(key)
        if product_match is not None:
            product_id = product_id or product_match.group(1)
            product = value

    return product_id, product, color_refs


def _variations(color_refs: Any) -> list[dict[str, Any]]:
    if not isinstance(color_refs, dict):
        return []
    references = color_refs.get("color_references")
    if not isinstance(references, list):
        return []

    result: list[dict[str, Any]] = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        color = _as_str(reference.get("color"))
        variations = reference.get("variations")
        if not isinstance(variations, list):
            continue
        for variation in variations:
            if isinstance(variation, dict):
                result.append({"color": color, "variation": variation})
    return result


def _prices(variation: dict[str, Any]) -> dict[str, Any]:
    prices = variation.get("prices")
    return prices if isinstance(prices, dict) else {}


def _variant_price(item: dict[str, Any]) -> Decimal | None:
    return _to_decimal(_prices(item["variation"]).get("price"))


def _variant_old_price(item: dict[str, Any]) -> Decimal | None:
    return _to_decimal(_prices(item["variation"]).get("old_price"))


def _lowest_price(variations: list[dict[str, Any]]) -> Decimal | None:
    prices = [
        price
        for item in variations
        for price in [_variant_price(item)]
        if price is not None
    ]
    return min(prices) if prices else None


def _first_old_price(variations: list[dict[str, Any]]) -> Decimal | None:
    for item in variations:
        old_price = _variant_old_price(item)
        if old_price is not None and old_price > 0:
            return old_price
    return None


def _variant_available(variation: dict[str, Any]) -> bool:
    if _forced_out_of_stock(variation.get("out_of_stock_forced")):
        return False
    quantity = _to_int(variation.get("quantity"))
    return quantity is not None and quantity > 0


def _forced_out_of_stock(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip().casefold()
    return text not in {"", "0", "false", "null", "none"}


def _availability(
    variations: list[dict[str, Any]],
) -> ProductAvailability | None:
    if not variations:
        return None
    if any(_variant_available(item["variation"]) for item in variations):
        return "in_stock"
    return "out_of_stock"


def _sizes(variations: list[dict[str, Any]]) -> list[str]:
    sizes = {
        size
        for item in variations
        for size in [_as_str(item["variation"].get("name"))]
        if size is not None
    }
    return sorted(sizes, key=_size_sort_key)


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)


def _image(
    color_refs: Any,
    soup: BeautifulSoup,
    url: str,
) -> str | None:
    default_id = None
    if isinstance(color_refs, dict):
        default_id = _as_str(color_refs.get("general_default_id_product_attribute"))

    variations = _variations(color_refs)
    ordered = sorted(
        variations,
        key=lambda item: (
            _as_str(item["variation"].get("id_product_attribute")) != default_id
        ),
    )
    for item in ordered:
        image = _first_image(item["variation"].get("images"))
        if image is not None:
            return image

    meta = soup.select_one('meta[property="og:image"]')
    if meta is None:
        return None
    value = _as_str(meta.get("content"))
    return urljoin(url, value) if value is not None else None


def _first_image(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        for key in ("large_default", "medium_default", "small_default"):
            image = _as_str(item.get(key))
            if image is not None:
                return image
    return None


def _variant_data(variations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in variations:
        color = item["color"]
        variation = item["variation"]
        entry: dict[str, Any] = {}
        if color is not None:
            entry["color"] = color

        variant_id = _as_str(variation.get("id_product_attribute"))
        if variant_id is not None:
            entry["variant_id"] = variant_id
        size = _as_str(variation.get("name"))
        if size is not None:
            entry["size"] = size
        quantity = _to_int(variation.get("quantity"))
        if quantity is not None:
            entry["quantity"] = quantity
        entry["availability"] = (
            "in_stock" if _variant_available(variation) else "out_of_stock"
        )

        price = _variant_price(item)
        if price is not None:
            entry["price"] = float(price)
        old_price = _variant_old_price(item)
        if old_price is not None:
            entry["old_price"] = float(old_price)
        reference = _as_str(variation.get("reference"))
        if reference is not None:
            entry["reference"] = reference

        result.append(entry)
    return result


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value).strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

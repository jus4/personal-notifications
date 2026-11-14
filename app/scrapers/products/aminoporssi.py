import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import get_settings
from app.models.source import Source
from app.scrapers.base import (
    ProductAvailability,
    ProductSourceAdapter,
    ScrapedProduct,
)
from app.scrapers.config import resolve_http_profile
from app.scrapers.http import fetch_json, validate_url
from app.scrapers.registry import register_product_adapter

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "EUR"


@register_product_adapter
class AminoporssiProductAdapter(ProductSourceAdapter):
    key = "aminoporssi_shopify"

    async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
        target = url or source.url
        if not target:
            raise ValueError(f"Source {source.slug!r} has no URL configured")

        validate_url(target)
        profile = resolve_http_profile(
            source,
            get_settings(),
            html=False,
            allow_default_proxy=False,
        )
        json_url = _product_json_url(target)
        payload = await fetch_json(json_url, profile=profile)

        product = self.parse(payload, target)
        if product is None:
            raise ValueError(
                f"Could not find product data for {target!r} (source {source.slug!r})"
            )
        return [product]

    @classmethod
    def parse(cls, payload: Any, url: str) -> ScrapedProduct | None:
        if not isinstance(payload, dict):
            logger.warning("Expected a Shopify product object from %s", url)
            return None

        external_id = _as_str(payload.get("id"))
        title = _as_str(payload.get("title"))
        if not external_id or not title:
            logger.warning("Could not extract product identity from %s", url)
            return None

        variants = payload.get("variants")
        if not isinstance(variants, list):
            variants = []
        options = payload.get("options")
        if not isinstance(options, list):
            options = []

        price = _price_from_cents(payload.get("price_min"))
        if price is None:
            price = _price_from_cents(payload.get("price"))
        if price is None:
            price = _min_variant_price(variants)

        compare_at_price = _positive_price(payload.get("compare_at_price_min"))
        if compare_at_price is None:
            compare_at_price = _positive_price(payload.get("compare_at_price"))

        image_url = _normalize_image_url(payload.get("featured_image"))
        if image_url is None:
            image_url = _first_image(payload.get("images"))

        raw_data = cls._raw_data(
            url=url,
            payload=payload,
            external_id=external_id,
            options=options,
            variants=variants,
            price=price,
            compare_at_price=compare_at_price,
        )

        return ScrapedProduct(
            external_id=external_id,
            title=title,
            url=url,
            price=price,
            currency=DEFAULT_CURRENCY if price is not None else None,
            availability=_availability(payload, variants),
            image_url=image_url,
            sizes=_sizes(options),
            raw_data=raw_data,
        )

    @staticmethod
    def _raw_data(
        *,
        url: str,
        payload: dict[str, Any],
        external_id: str,
        options: list[Any],
        variants: list[Any],
        price: Decimal | None,
        compare_at_price: Decimal | None,
    ) -> dict[str, Any]:
        raw_data: dict[str, Any] = {
            "link": url,
            "currency": DEFAULT_CURRENCY,
            "product_id": external_id,
            "options": _options(options),
            "variants": _variants(variants),
        }

        handle = _as_str(payload.get("handle"))
        if handle is not None:
            raw_data["handle"] = handle
        vendor = _as_str(payload.get("vendor"))
        if vendor is not None:
            raw_data["vendor"] = vendor
        product_type = _as_str(payload.get("type"))
        if product_type is not None:
            raw_data["product_type"] = product_type
        if price is not None:
            raw_data["price"] = float(price)
        if compare_at_price is not None:
            raw_data["compare_at_price"] = float(compare_at_price)

        return raw_data


def _product_json_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path.endswith(".js"):
        path = f"{path}.js"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _availability(
    payload: dict[str, Any],
    variants: list[Any],
) -> ProductAvailability | None:
    available = payload.get("available")
    if isinstance(available, bool):
        return "in_stock" if available else "out_of_stock"

    variant_values = [
        variant.get("available")
        for variant in variants
        if isinstance(variant, dict) and isinstance(variant.get("available"), bool)
    ]
    if any(variant_values):
        return "in_stock"
    if variant_values:
        return "out_of_stock"
    return None


def _sizes(options: list[Any]) -> list[str]:
    sizes: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        name = _as_str(option.get("name"))
        if name is None or not _is_size_option(name):
            continue
        values = option.get("values")
        if not isinstance(values, list):
            continue
        for value in values:
            size = _as_str(value)
            if size is not None:
                sizes.add(size)
    return sorted(sizes, key=_size_sort_key)


def _is_size_option(name: str) -> bool:
    normalized = name.casefold()
    return "koko" in normalized or "size" in normalized


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)


def _options(options: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        entry: dict[str, Any] = {}
        name = _as_str(option.get("name"))
        if name is not None:
            entry["name"] = name
        values = option.get("values")
        if isinstance(values, list):
            cleaned = [value for value in (_as_str(item) for item in values) if value]
            if cleaned:
                entry["values"] = cleaned
        if entry:
            result.append(entry)
    return result


def _variants(variants: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue

        entry: dict[str, Any] = {}
        variant_id = _as_str(variant.get("id"))
        if variant_id is not None:
            entry["variant_id"] = variant_id
        title = _as_str(variant.get("title"))
        if title is not None:
            entry["title"] = title
        sku = _as_str(variant.get("sku"))
        if sku is not None:
            entry["sku"] = sku
        barcode = _as_str(variant.get("barcode"))
        if barcode is not None:
            entry["barcode"] = barcode

        values = variant.get("options")
        if isinstance(values, list):
            cleaned = [value for value in (_as_str(item) for item in values) if value]
            if cleaned:
                entry["options"] = cleaned

        price = _price_from_cents(variant.get("price"))
        if price is not None:
            entry["price"] = float(price)
        compare_at_price = _positive_price(variant.get("compare_at_price"))
        if compare_at_price is not None:
            entry["compare_at_price"] = float(compare_at_price)

        available = variant.get("available")
        if isinstance(available, bool):
            entry["available"] = available

        result.append(entry)
    return result


def _min_variant_price(variants: list[Any]) -> Decimal | None:
    prices = [
        price
        for variant in variants
        if isinstance(variant, dict)
        for price in [_price_from_cents(variant.get("price"))]
        if price is not None
    ]
    return min(prices) if prices else None


def _price_from_cents(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        cents = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not cents.is_finite():
        return None
    return (cents / Decimal(100)).quantize(Decimal("0.01"))


def _positive_price(value: Any) -> Decimal | None:
    price = _price_from_cents(value)
    if price is None or price <= 0:
        return None
    return price


def _normalize_image_url(value: Any) -> str | None:
    text = _as_str(value)
    if text is None:
        return None
    if text.startswith("//"):
        return f"https:{text}"
    return text


def _first_image(value: Any) -> str | None:
    if not isinstance(value, list):
        return _normalize_image_url(value)
    for item in value:
        image = _normalize_image_url(item)
        if image is not None:
            return image
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

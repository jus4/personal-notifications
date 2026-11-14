import json
import logging
import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
PRODUCT_ID_RE = re.compile(r"/(\d+)$")
IN_STOCK_VALUES = frozenset(
    {"instock", "onlineonly", "instoreonly", "limitedavailability"}
)
OUT_OF_STOCK_VALUES = frozenset({"outofstock", "soldout", "discontinued"})


@register_product_adapter
class TradeinnProductAdapter(ProductSourceAdapter):
    key = "tradeinn_html"

    async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
        target = url or source.url
        if not target:
            raise ValueError(f"Source {source.slug!r} has no URL configured")

        validate_url(target)
        product_url = _normalize_product_url(target)
        profile = resolve_http_profile(
            source,
            get_settings(),
            html=True,
            allow_default_proxy=False,
        )

        async with text_session(profile) as session:
            html = await fetch_text(product_url, session=session, profile=profile)

        product = self.parse(html, product_url)
        if product is None:
            raise ValueError(
                f"Could not find product data for {target!r} (source {source.slug!r})"
            )
        return [product]

    @classmethod
    def parse(cls, html: str, url: str) -> ScrapedProduct | None:
        soup = BeautifulSoup(html, "html.parser")
        entity = _product_entity(soup)
        if entity is None:
            logger.warning("Could not find product JSON-LD in %s", url)
            return None

        variants = _variants(entity)
        external_id = (
            _as_str(entity.get("sku"))
            or _variant_group_id(variants)
            or _id_from_url(url)
        )
        title = _as_str(entity.get("name"))
        if not external_id or not title:
            logger.warning("Could not extract product identity from %s", url)
            return None

        price = _lowest_price(entity, variants)
        currency = _currency(entity, variants)
        if currency is None and price is not None:
            currency = DEFAULT_CURRENCY

        return ScrapedProduct(
            external_id=external_id,
            title=title,
            url=url,
            price=price,
            currency=currency,
            availability=_availability(entity, variants),
            image_url=_first_image(entity.get("image")),
            sizes=_sizes(variants),
            raw_data=cls._raw_data(
                url=url,
                entity=entity,
                external_id=external_id,
                variants=variants,
                price=price,
                currency=currency,
            ),
        )

    @staticmethod
    def _raw_data(
        *,
        url: str,
        entity: dict[str, Any],
        external_id: str,
        variants: list[dict[str, Any]],
        price: Decimal | None,
        currency: str | None,
    ) -> dict[str, Any]:
        raw_data: dict[str, Any] = {
            "link": url,
            "currency": currency or DEFAULT_CURRENCY,
            "product_id": external_id,
            "variants": _variant_data(variants),
        }

        mpn = _as_str(entity.get("mpn"))
        if mpn is not None:
            raw_data["mpn"] = mpn
        brand = _brand_name(entity.get("brand"))
        if brand is not None:
            raw_data["brand"] = brand
        category = _as_str(entity.get("category"))
        if category is not None:
            raw_data["category"] = category
        rating = _rating(entity.get("aggregateRating"))
        if rating is not None:
            raw_data["rating"] = rating
        if price is not None:
            raw_data["price"] = float(price)

        return raw_data


def _normalize_product_url(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if PRODUCT_ID_RE.search(path) and not path.endswith("/p"):
        path = f"{path}/p"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _product_entity(soup: BeautifulSoup) -> dict[str, Any] | None:
    fallback: dict[str, Any] | None = None
    for payload in _json_ld_payloads(soup):
        entity = _find_product_entity(payload)
        if entity is None:
            continue
        if "ProductGroup" in _types(entity.get("@type")):
            return entity
        if fallback is None:
            fallback = entity
    return fallback


def _json_ld_payloads(soup: BeautifulSoup) -> Iterator[Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string if script.string is not None else script.get_text()
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed JSON-LD block")


def _find_product_entity(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for item in payload:
            entity = _find_product_entity(item)
            if entity is not None:
                return entity
        return None
    if not isinstance(payload, dict):
        return None

    types = _types(payload.get("@type"))
    if "ProductGroup" in types or "Product" in types:
        return payload

    for key in ("mainEntity", "@graph"):
        if key not in payload:
            continue
        entity = _find_product_entity(payload[key])
        if entity is not None:
            return entity
    return None


def _types(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _variants(entity: dict[str, Any]) -> list[dict[str, Any]]:
    variants = entity.get("hasVariant")
    if isinstance(variants, dict):
        variants = [variants]
    if isinstance(variants, list):
        result = [variant for variant in variants if isinstance(variant, dict)]
        if result:
            return result
    if "Product" in _types(entity.get("@type")):
        return [entity]
    return []


def _offer(variant: dict[str, Any]) -> dict[str, Any]:
    offers = variant.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    return offers if isinstance(offers, dict) else {}


def _lowest_price(
    entity: dict[str, Any],
    variants: list[dict[str, Any]],
) -> Decimal | None:
    prices = [
        price
        for variant in variants
        for price in [_price(variant)]
        if price is not None
    ]
    if prices:
        return min(prices)
    return _price(entity)


def _price(entity: dict[str, Any]) -> Decimal | None:
    return _to_decimal(_offer(entity).get("price"))


def _currency(
    entity: dict[str, Any],
    variants: list[dict[str, Any]],
) -> str | None:
    for variant in variants:
        currency = _as_str(_offer(variant).get("priceCurrency"))
        if currency is not None:
            return currency.upper()
    currency = _as_str(_offer(entity).get("priceCurrency"))
    return currency.upper() if currency is not None else None


def _availability(
    entity: dict[str, Any],
    variants: list[dict[str, Any]],
) -> ProductAvailability | None:
    statuses = [
        status
        for variant in variants
        for status in [_normalize_availability(_offer(variant).get("availability"))]
        if status is not None
    ]
    if not statuses:
        return _normalize_availability(_offer(entity).get("availability"))
    if "in_stock" in statuses:
        return "in_stock"
    if all(status == "out_of_stock" for status in statuses):
        return "out_of_stock"
    return "unknown"


def _normalize_availability(value: Any) -> ProductAvailability | None:
    text = _as_str(value)
    if text is None:
        return None
    token = text.rsplit("/", 1)[-1].casefold()
    if token in IN_STOCK_VALUES:
        return "in_stock"
    if token in OUT_OF_STOCK_VALUES:
        return "out_of_stock"
    return "unknown"


def _sizes(variants: list[dict[str, Any]]) -> list[str]:
    sizes = {
        size
        for variant in variants
        for size in [_as_str(variant.get("size"))]
        if size is not None
    }
    return sorted(sizes, key=_size_sort_key)


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)


def _variant_group_id(variants: list[dict[str, Any]]) -> str | None:
    for variant in variants:
        group_id = _as_str(variant.get("productGroupID"))
        if group_id is not None:
            return group_id
    return None


def _id_from_url(url: str) -> str | None:
    path = urlsplit(url).path.rstrip("/")
    match = PRODUCT_ID_RE.search(path)
    return match.group(1) if match is not None else None


def _variant_data(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for variant in variants:
        entry: dict[str, Any] = {}
        size = _as_str(variant.get("size"))
        if size is not None:
            entry["size"] = size
        color = _as_str(variant.get("color"))
        if color is not None:
            entry["color"] = color
        gtin = _as_str(variant.get("gtin13"))
        if gtin is not None:
            entry["gtin13"] = gtin

        offer = _offer(variant)
        price = _to_decimal(offer.get("price"))
        if price is not None:
            entry["price"] = float(price)
        currency = _as_str(offer.get("priceCurrency"))
        if currency is not None:
            entry["currency"] = currency.upper()
        availability = _normalize_availability(offer.get("availability"))
        if availability is not None:
            entry["availability"] = availability
        image = _first_image(variant.get("image"))
        if image is not None:
            entry["image"] = image

        result.append(entry)
    return result


def _brand_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return _as_str(value.get("name"))
    return _as_str(value)


def _rating(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    rating: dict[str, str] = {}
    rating_value = _as_str(value.get("ratingValue"))
    rating_count = _as_str(value.get("reviewCount") or value.get("ratingCount"))
    if rating_value is not None:
        rating["value"] = rating_value
    if rating_count is not None:
        rating["count"] = rating_count
    return rating or None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _first_image(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            image = _first_image(item)
            if image is not None:
                return image
        return None
    if isinstance(value, dict):
        return _as_str(value.get("url"))
    return _as_str(value)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

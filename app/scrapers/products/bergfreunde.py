import json
import logging
import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

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

ARTICLE_RE = re.compile(r"window\.oBfArticle\['[^']+'\]\s*=\s*(\{.*?\});", re.DOTALL)
DEFAULT_CURRENCY = "EUR"


@register_product_adapter
class BergfreundeProductAdapter(ProductSourceAdapter):
    key = "bergfreunde_html"

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
        product_json = cls._product_json_ld(soup)
        article = cls._article_data(html)
        main = article.get("main")
        if not isinstance(main, dict):
            main = {}
        variants = article.get("variants")
        if not isinstance(variants, dict):
            variants = {}

        details = soup.select_one("#details[data-sku]")
        external_id = (
            _as_str(product_json.get("sku"))
            or _as_str(details.get("data-sku") if details is not None else None)
            or _slug_from_url(url)
        )
        title = _as_str(product_json.get("name")) or _page_title(soup)
        if not external_id or not title:
            logger.warning("Could not extract product identity from %s", url)
            return None

        stock = main.get("stockStatus")
        price = _to_decimal(main.get("price"))
        list_price = _to_decimal(main.get("tprice"))
        image_url = (
            _first_image(product_json.get("image"))
            or _as_str(main.get("largeimage"))
            or _as_str(main.get("detailimage"))
        )
        raw_data = cls._raw_data(
            url=url,
            main=main,
            stock=stock,
            variants=variants,
            product_json=product_json,
            price=price,
            list_price=list_price,
        )

        return ScrapedProduct(
            external_id=external_id,
            title=title,
            url=url,
            price=price,
            currency=DEFAULT_CURRENCY if price is not None else None,
            availability=_availability(stock),
            image_url=image_url,
            sizes=_sizes(variants),
            raw_data=raw_data,
        )

    @classmethod
    def _raw_data(
        cls,
        *,
        url: str,
        main: dict[str, Any],
        stock: Any,
        variants: dict[str, Any],
        product_json: dict[str, Any],
        price: Decimal | None,
        list_price: Decimal | None,
    ) -> dict[str, Any]:
        raw_data: dict[str, Any] = {
            "link": url,
            "currency": DEFAULT_CURRENCY,
            "variants": _variants(variants),
        }

        if price is not None:
            raw_data["price"] = float(price)
        if list_price is not None:
            raw_data["list_price"] = float(list_price)

        formatted_price = _as_str(main.get("priceFormatted"))
        if formatted_price is not None:
            raw_data["price_formatted"] = formatted_price
        formatted_list_price = _as_str(main.get("tpriceFormatted"))
        if formatted_list_price is not None:
            raw_data["list_price_formatted"] = formatted_list_price

        discount = _as_int(main.get("percent"))
        if discount is not None:
            raw_data["discount_percent"] = discount

        if isinstance(stock, dict):
            stock_text = _as_str(stock.get("text"))
            if stock_text is not None:
                raw_data["stock_text"] = stock_text
            stock_quantity = _as_int(stock.get("oxStock"))
            if stock_quantity is not None:
                raw_data["stock_quantity"] = stock_quantity

        rating = product_json.get("aggregateRating")
        if isinstance(rating, dict):
            rating_data = {}
            rating_value = _as_str(rating.get("ratingValue"))
            rating_count = _as_str(rating.get("ratingCount"))
            if rating_value is not None:
                rating_data["value"] = rating_value
            if rating_count is not None:
                rating_data["count"] = rating_count
            if rating_data:
                raw_data["rating"] = rating_data

        return raw_data

    @classmethod
    def _product_json_ld(cls, soup: BeautifulSoup) -> dict[str, Any]:
        for payload in cls._json_ld_payloads(soup):
            if cls._is_product(payload):
                return payload
        return {}

    @staticmethod
    def _json_ld_payloads(soup: BeautifulSoup) -> Iterator[Any]:
        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string if script.string is not None else script.get_text()
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON-LD block")
                continue
            if isinstance(payload, list):
                yield from payload
            else:
                yield payload

    @staticmethod
    def _is_product(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        types = payload.get("@type")
        if isinstance(types, str):
            return types == "Product"
        if isinstance(types, list):
            return "Product" in types
        return False

    @staticmethod
    def _article_data(html: str) -> dict[str, Any]:
        match = ARTICLE_RE.search(html)
        if match is None:
            return {}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed window.oBfArticle payload")
            return {}
        return payload if isinstance(payload, dict) else {}


def _sizes(variants: dict[str, Any]) -> list[str]:
    sizes: set[str] = set()
    for variant in variants.values():
        if not isinstance(variant, dict):
            continue
        attributes = variant.get("variants")
        if not isinstance(attributes, dict):
            continue
        size = _as_str(attributes.get("size"))
        if size is not None:
            sizes.add(size)
    return sorted(sizes, key=_size_sort_key)


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)


def _variants(variants: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for variant in variants.values():
        if not isinstance(variant, dict):
            continue

        stock = variant.get("stockStatus")
        attributes = variant.get("variants")
        if not isinstance(attributes, dict):
            attributes = {}

        entry: dict[str, Any] = {}
        variant_id = _as_str(variant.get("oxid"))
        if variant_id is not None:
            entry["variant_id"] = variant_id
        ean = _as_str(variant.get("ean"))
        if ean is not None:
            entry["ean"] = ean
        color = _as_str(attributes.get("color"))
        if color is not None:
            entry["color"] = color
        size = _as_str(attributes.get("size"))
        if size is not None:
            entry["size"] = size

        price = _to_decimal(variant.get("price"))
        if price is not None:
            entry["price"] = float(price)
        list_price = _to_decimal(variant.get("tprice"))
        if list_price is not None:
            entry["list_price"] = float(list_price)
        price_formatted = _as_str(variant.get("priceFormatted"))
        if price_formatted is not None:
            entry["price_formatted"] = price_formatted
        list_price_formatted = _as_str(variant.get("tpriceFormatted"))
        if list_price_formatted is not None:
            entry["list_price_formatted"] = list_price_formatted

        availability = _availability(stock)
        if availability is not None:
            entry["availability"] = availability

        if isinstance(stock, dict):
            stock_text = _as_str(stock.get("text"))
            if stock_text is not None:
                entry["stock_text"] = stock_text
            stock_quantity = _as_int(stock.get("oxStock"))
            if stock_quantity is not None:
                entry["stock_quantity"] = stock_quantity

        result.append(entry)
    return result


def _availability(stock: Any) -> ProductAvailability | None:
    if not isinstance(stock, dict):
        return None
    buyable = stock.get("isBuyable")
    if isinstance(buyable, bool):
        return "in_stock" if buyable else "out_of_stock"
    if isinstance(buyable, int):
        return "in_stock" if buyable else "out_of_stock"
    return "unknown"


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_image(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            image = _as_str(item)
            if image is not None:
                return image
        return None
    return _as_str(value)


def _page_title(soup: BeautifulSoup) -> str | None:
    heading = soup.select_one("h1")
    if heading is not None:
        title = heading.get_text(" ", strip=True)
        if title:
            return title
    meta = soup.select_one('meta[property="og:title"]')
    if meta is not None:
        return _as_str(meta.get("content"))
    return None


def _slug_from_url(url: str) -> str | None:
    path = urlsplit(url).path.rstrip("/")
    if not path:
        return None
    slug = path.rsplit("/", 1)[-1]
    return slug or None

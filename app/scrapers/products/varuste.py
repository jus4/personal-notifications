import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

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
PRODUCT_ID_RE = re.compile(r"/p(\d+)")
IN_STOCK_VALUES = frozenset(
    {"instock", "onlineonly", "instoreonly", "limitedavailability"}
)
OUT_OF_STOCK_VALUES = frozenset({"outofstock", "soldout", "discontinued"})


@register_product_adapter
class VarusteProductAdapter(ProductSourceAdapter):
    key = "varuste_html"

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
        external_id = _external_id(soup, url)
        title = _text(soup.select_one('h1[itemprop="name"]'))
        if not external_id or not title:
            logger.warning("Could not extract product identity from %s", url)
            return None

        price = _price(soup)
        currency = _currency(soup)
        if currency is None and price is not None:
            currency = DEFAULT_CURRENCY
        color, color_id, sizes, variants = _properties(soup)

        return ScrapedProduct(
            external_id=external_id,
            title=title,
            url=url,
            price=price,
            currency=currency,
            availability=_availability(soup),
            image_url=_image(soup, url),
            sizes=sizes,
            raw_data=cls._raw_data(
                url=url,
                soup=soup,
                external_id=external_id,
                price=price,
                currency=currency,
                color=color,
                color_id=color_id,
                variants=variants,
            ),
        )

    @staticmethod
    def _raw_data(
        *,
        url: str,
        soup: BeautifulSoup,
        external_id: str,
        price: Decimal | None,
        currency: str | None,
        color: str | None,
        color_id: str | None,
        variants: list[dict[str, str]],
    ) -> dict[str, Any]:
        raw_data: dict[str, Any] = {
            "link": url,
            "currency": currency or DEFAULT_CURRENCY,
            "product_id": external_id,
            "variants": variants,
        }

        brand = _text(soup.select_one('[itemprop="brand"]'))
        if brand is not None:
            raw_data["brand"] = brand
        if color is not None:
            raw_data["color"] = color
        if color_id is not None:
            raw_data["color_id"] = color_id
        if price is not None:
            raw_data["price"] = float(price)

        return raw_data


def _external_id(soup: BeautifulSoup, url: str) -> str | None:
    product_id = soup.select_one('input[name="_tuoteid"]')
    if product_id is not None:
        value = _as_str(product_id.get("value"))
        if value is not None:
            return value

    match = PRODUCT_ID_RE.search(urlsplit(url).path)
    return match.group(1) if match is not None else None


def _price(soup: BeautifulSoup) -> Decimal | None:
    meta = soup.select_one('meta[itemprop="price"]')
    if meta is not None:
        price = _to_decimal(meta.get("content"))
        if price is not None:
            return price
    return _to_decimal(_text(soup.select_one("#price_text")))


def _currency(soup: BeautifulSoup) -> str | None:
    meta = soup.select_one('meta[itemprop="priceCurrency"]')
    if meta is None:
        return None
    currency = _as_str(meta.get("content"))
    return currency.upper() if currency is not None else None


def _availability(soup: BeautifulSoup) -> ProductAvailability | None:
    link = soup.select_one('link[itemprop="availability"]')
    if link is not None:
        status = _normalize_availability(link.get("href"))
        if status is not None:
            return status

    form = soup.find("form", id="lomake")
    if form is not None and form.select_one(".varastossa") is not None:
        return "in_stock"
    return None


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


def _image(soup: BeautifulSoup, url: str) -> str | None:
    image = soup.select_one('img[itemprop="image"]')
    if image is None:
        return None
    value = _as_str(image.get("src")) or _as_str(image.get("data-src"))
    return urljoin(url, value) if value is not None else None


def _properties(
    soup: BeautifulSoup,
) -> tuple[str | None, str | None, list[str], list[dict[str, str]]]:
    form = soup.find("form", id="lomake")
    if form is None:
        return None, None, [], []

    color: str | None = None
    color_id: str | None = None
    sizes: set[str] = set()
    variants: list[dict[str, str]] = []

    for selection in form.find_all(
        "div",
        class_="property_selection",
        recursive=False,
    ):
        name = _text(selection.select_one(".property_type_name")) or ""
        normalized = name.casefold()

        if "väri" in normalized or "color" in normalized:
            selected = selection.select_one(".select_option.selected")
            if selected is not None:
                color = _text(selected) or _image_alt(selected)
                color_id = _as_str(selected.get("data-option-key"))

        if "koko" in normalized or "size" in normalized:
            for item in selection.select(".ddownlistitem[data-value]"):
                value = _as_str(item.get("data-value"))
                if value is None or value == "-":
                    continue
                label = item.select_one('div[id^="property_name"]')
                size = _text(label) or _text(item)
                if size is None:
                    continue
                sizes.add(size)
                variants.append({"size": size, "property_value_id": value})

    sorted_sizes = sorted(sizes, key=_size_sort_key)
    return color, color_id, sorted_sizes, variants


def _image_alt(container: Any) -> str | None:
    image = container.select_one("img")
    return _as_str(image.get("alt")) if image is not None else None


def _size_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value.replace(",", ".")), value)
    except ValueError:
        return (1, 0.0, value)


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


def _text(element: Any) -> str | None:
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return text or None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

from app.models.product import Product
from app.models.product_source import ProductSource
from app.scrapers.base import ScrapedProduct
from app.services.product_scrape import compare_scraped_product


def _product(title: str = "La Sportiva Solution") -> Product:
    return Product(title=title)


def _link(external_id: str | None = None) -> ProductSource:
    return ProductSource(
        product_id=1,
        source_id=1,
        url="https://example.test/product",
        external_id=external_id,
    )


def test_compare_by_external_id() -> None:
    result = compare_scraped_product(
        _product(),
        _link(external_id="301-0448"),
        ScrapedProduct(
            external_id="301-0448",
            title="Different title",
        ),
    )

    assert result.matched is True
    assert result.reason == "external_id"


def test_compare_falls_back_to_title_when_external_id_differs() -> None:
    result = compare_scraped_product(
        _product(),
        _link(external_id="old-id"),
        ScrapedProduct(
            external_id="new-id",
            title="La Sportiva Solution",
        ),
    )

    assert result.matched is True
    assert result.reason == "title"


def test_compare_by_title_with_punctuation_and_extra_words() -> None:
    result = compare_scraped_product(
        _product(),
        _link(),
        ScrapedProduct(
            external_id="other-id",
            title="LA SPORTIVA - Solution (Women's) Climbing Shoes",
        ),
    )

    assert result.matched is True
    assert result.reason == "title"


def test_compare_reports_mismatch() -> None:
    result = compare_scraped_product(
        _product(),
        _link(),
        ScrapedProduct(
            external_id="other-id",
            title="Scarpa Instinct",
        ),
    )

    assert result.matched is False
    assert result.reason == "mismatch"

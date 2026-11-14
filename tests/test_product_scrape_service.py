import asyncio
from datetime import datetime
from decimal import Decimal

import pytest
from sqlmodel import Session

from app.models.common import utcnow
from app.models.product_source import ProductSource
from app.models.source import Source
from app.scrapers.base import ScrapedProduct
from app.scrapers.registry import PRODUCT_ADAPTERS
from app.services.product_scrape import run_product_scrape, run_product_source
from app.services.products import ProductRepository


def _source(session: Session) -> Source:
    source = Source(
        slug="bergfreunde",
        kind="products",
        adapter="fake-product",
        enabled=True,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _link(
    session: Session,
    source: Source,
    *,
    url: str = "https://example.test/solution",
    last_scraped_at: datetime | None = None,
) -> ProductSource:
    product = ProductRepository(session).create_product(title="Solution")
    link = ProductSource(
        product_id=product.id or 0,
        source_id=source.id or 0,
        url=url,
        sizes=[],
        last_scraped_at=last_scraped_at,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def test_run_product_source_updates_scraped_link(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    source = _source(session)
    link = _link(session, source)

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            assert url == link.url
            return [
                ScrapedProduct(
                    external_id="301-0448",
                    title="La Sportiva Solution",
                    price=Decimal("131.96"),
                    currency="EUR",
                    availability="in_stock",
                    sizes=["40", "41"],
                )
            ]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)

    result = asyncio.run(run_product_source(session, source, force=True))

    assert result.links == 1
    assert result.updated == 1
    assert result.failed == 0

    session.refresh(link)
    assert link.external_id == "301-0448"
    assert link.price == Decimal("131.96")
    assert link.sizes == ["40", "41"]
    assert link.last_scraped_at is not None
    assert link.last_error is None


def test_run_product_scrape_skips_fresh_links_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    source = _source(session)
    _link(
        session,
        source,
        last_scraped_at=utcnow(),
    )
    calls = 0

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            nonlocal calls
            calls += 1
            return [ScrapedProduct(external_id="1", title="Solution")]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)

    skipped = asyncio.run(run_product_scrape(session, interval_seconds=60 * 60 * 24))
    assert skipped.results[0].links == 0
    assert calls == 0

    forced = asyncio.run(run_product_scrape(session, force=True))
    assert forced.results[0].links == 1
    assert calls == 1


def test_run_product_source_isolates_link_failures(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    source = _source(session)
    failing = _link(session, source, url="https://example.test/bad")
    good = _link(session, source, url="https://example.test/good")

    class FakeAdapter:
        key = "fake-product"

        async def fetch(self, source: Source, url: str) -> list[ScrapedProduct]:
            if url == failing.url:
                raise RuntimeError("boom")
            return [ScrapedProduct(external_id="good", title="Good product")]

    monkeypatch.setitem(PRODUCT_ADAPTERS, FakeAdapter.key, FakeAdapter)

    result = asyncio.run(run_product_source(session, source, force=True))

    assert result.updated == 1
    assert result.failed == 1
    assert "boom" in result.errors[0]

    session.refresh(failing)
    session.refresh(good)
    assert failing.last_error == "boom"
    assert good.external_id == "good"


def test_run_product_scrape_reports_missing_adapter(session: Session) -> None:
    _source(session)

    batch = asyncio.run(run_product_scrape(session, force=True))

    assert batch.source_count == 1
    assert len(batch.failures) == 1
    assert "No product adapter registered" in batch.failures[0].error

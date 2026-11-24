import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.services import scrape_scheduler
from app.services.scrape_scheduler import (
    in_active_window,
    next_job_run,
    next_product_run,
    next_scheduled_run,
)

JOB_INTERVAL = 60 * 60 * 3
PRODUCT_INTERVAL = 60 * 60 * 24
WINTER_START_UTC = datetime(2026, 1, 15, 4, 0, tzinfo=UTC)
WINTER_END_UTC = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)


class StopScheduler(Exception):
    pass


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite://",
        "scrape_interval_seconds": JOB_INTERVAL,
        "scrape_active_start_hour": 6,
        "scrape_active_end_hour": 21,
        "product_scrape_interval_seconds": PRODUCT_INTERVAL,
    }
    values.update(overrides)
    return Settings.model_construct(**values)


def test_next_job_run_before_active_window() -> None:
    now = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)

    result = next_job_run(now, start_hour=6, end_hour=21, interval_seconds=JOB_INTERVAL)

    assert result == WINTER_START_UTC


def test_next_job_run_between_slots() -> None:
    now = datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

    result = next_job_run(now, start_hour=6, end_hour=21, interval_seconds=JOB_INTERVAL)

    assert result == datetime(2026, 1, 15, 7, 0, tzinfo=UTC)


def test_next_job_run_after_active_window_returns_next_morning() -> None:
    now = datetime(2026, 1, 15, 19, 30, tzinfo=UTC)

    result = next_job_run(now, start_hour=6, end_hour=21, interval_seconds=JOB_INTERVAL)

    assert result == datetime(2026, 1, 16, 4, 0, tzinfo=UTC)


def test_next_job_run_uses_helsinki_summer_time() -> None:
    now = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)

    result = next_job_run(now, start_hour=6, end_hour=21, interval_seconds=JOB_INTERVAL)

    assert result == datetime(2026, 7, 15, 3, 0, tzinfo=UTC)


def test_next_product_run_today_and_tomorrow() -> None:
    before_start = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
    after_start = datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

    assert next_product_run(before_start, start_hour=6) == WINTER_START_UTC
    assert next_product_run(after_start, start_hour=6) == datetime(
        2026, 1, 16, 4, 0, tzinfo=UTC
    )


def test_next_scheduled_run_batches_jobs_and_products_at_start() -> None:
    now = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)

    result = next_scheduled_run(now, _settings())

    assert result is not None
    assert result.at == WINTER_START_UTC
    assert result.jobs is True
    assert result.products is True


def test_next_scheduled_run_only_jobs_between_slots() -> None:
    now = datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

    result = next_scheduled_run(now, _settings())

    assert result is not None
    assert result.at == datetime(2026, 1, 15, 7, 0, tzinfo=UTC)
    assert result.jobs is True
    assert result.products is False


def test_next_scheduled_run_respects_disabled_scrapers() -> None:
    now = datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

    jobs_only = next_scheduled_run(
        now,
        _settings(product_scrape_interval_seconds=0),
    )
    products_only = next_scheduled_run(
        now,
        _settings(scrape_interval_seconds=0),
    )
    disabled = next_scheduled_run(
        now,
        _settings(scrape_interval_seconds=0, product_scrape_interval_seconds=0),
    )

    assert jobs_only is not None
    assert jobs_only.jobs is True
    assert jobs_only.products is False
    assert products_only is not None
    assert products_only.jobs is False
    assert products_only.products is True
    assert disabled is None


def test_in_active_window() -> None:
    settings = _settings()

    assert in_active_window(WINTER_START_UTC, settings) is True
    assert in_active_window(WINTER_END_UTC, settings) is True
    assert in_active_window(datetime(2026, 1, 15, 20, 0, tzinfo=UTC), settings) is False


def test_scheduler_runs_catch_up_then_daytime_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(datetime(2026, 1, 15, 4, 5, tzinfo=UTC))
    sleeps: list[float] = []
    calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)
        if len(sleeps) >= 4:
            raise StopScheduler

    async def fake_jobs() -> None:
        calls.append("jobs")

    async def fake_products(settings: Settings) -> None:
        calls.append("products")

    monkeypatch.setattr(scrape_scheduler, "utcnow", clock.now)
    monkeypatch.setattr(scrape_scheduler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scrape_scheduler, "_run_jobs", fake_jobs)
    monkeypatch.setattr(scrape_scheduler, "_run_products", fake_products)

    with pytest.raises(StopScheduler):
        asyncio.run(scrape_scheduler.run_scrape_scheduler(_settings()))

    assert calls == ["jobs", "products", "jobs", "jobs", "jobs"]
    assert all(seconds >= 60 * 60 for seconds in sleeps)


def test_scheduler_does_not_run_during_night(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(datetime(2026, 1, 15, 20, 0, tzinfo=UTC))
    sleeps: list[float] = []
    calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)
        if len(sleeps) >= 2:
            raise StopScheduler

    async def fake_jobs() -> None:
        calls.append("jobs")

    async def fake_products(settings: Settings) -> None:
        calls.append("products")

    monkeypatch.setattr(scrape_scheduler, "utcnow", clock.now)
    monkeypatch.setattr(scrape_scheduler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scrape_scheduler, "_run_jobs", fake_jobs)
    monkeypatch.setattr(scrape_scheduler, "_run_products", fake_products)

    with pytest.raises(StopScheduler):
        asyncio.run(scrape_scheduler.run_scrape_scheduler(_settings()))

    assert calls == ["jobs", "products"]
    assert sleeps[0] == 8 * 60 * 60


def test_scheduler_exits_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock(datetime(2026, 1, 15, 4, 5, tzinfo=UTC))
    sleeps: list[float] = []
    calls: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_jobs() -> None:
        calls.append("jobs")

    async def fake_products(settings: Settings) -> None:
        calls.append("products")

    monkeypatch.setattr(scrape_scheduler, "utcnow", clock.now)
    monkeypatch.setattr(scrape_scheduler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scrape_scheduler, "_run_jobs", fake_jobs)
    monkeypatch.setattr(scrape_scheduler, "_run_products", fake_products)

    asyncio.run(
        scrape_scheduler.run_scrape_scheduler(
            _settings(scrape_interval_seconds=0, product_scrape_interval_seconds=0)
        )
    )

    assert sleeps == []
    assert calls == []


def test_run_due_skips_when_scrape_lock_is_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_jobs() -> None:
        calls.append("jobs")

    async def fake_products(settings: Settings) -> None:
        calls.append("products")

    class HeldLock:
        async def try_acquire(self) -> bool:
            return False

        async def release(self) -> None:
            raise AssertionError("release should not be called when lock is held")

    async def scenario() -> None:
        monkeypatch.setattr(scrape_scheduler, "scrape_lock", HeldLock())
        monkeypatch.setattr(scrape_scheduler, "_run_jobs", fake_jobs)
        monkeypatch.setattr(scrape_scheduler, "_run_products", fake_products)
        await scrape_scheduler._run_due(
            _settings(),
            jobs=True,
            products=True,
        )

    asyncio.run(scenario())

    assert calls == []

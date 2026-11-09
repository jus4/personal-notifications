from datetime import UTC, datetime

from app.core.templating import templates


def test_local_time_filters_convert_utc_to_helsinki() -> None:
    value = datetime(2026, 11, 5, 23, 30, tzinfo=UTC)

    assert templates.env.filters["local_datetime"](value) == "2026-11-06 01:30 EET"
    assert templates.env.filters["local_date"](value) == "2026-11-06"
    assert templates.env.filters["utc_isoformat"](value) == "2026-11-05T23:30:00+00:00"


def test_local_time_filters_treat_naive_values_as_utc() -> None:
    value = datetime(2026, 11, 5, 12, 0)

    assert templates.env.filters["local_datetime"](value) == "2026-11-05 14:00 EET"
    assert templates.env.filters["local_date"](value) == "2026-11-05"


def test_local_time_filters_handle_daylight_saving() -> None:
    value = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)

    assert templates.env.filters["local_datetime"](value) == "2026-07-06 15:00 EEST"


def test_local_time_filters_handle_missing_values() -> None:
    assert templates.env.filters["local_datetime"](None) == ""
    assert templates.env.filters["local_date"](None) == ""
    assert templates.env.filters["utc_isoformat"](None) == ""

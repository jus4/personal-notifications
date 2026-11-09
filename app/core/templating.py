from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from app.core.security import is_authenticated

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
DISPLAY_TIMEZONE = ZoneInfo("Europe/Helsinki")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return _as_utc(value).astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M %Z")


def local_date(value: datetime | None) -> str:
    if value is None:
        return ""
    return _as_utc(value).astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d")


def utc_isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    return _as_utc(value).isoformat()


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["is_authenticated"] = is_authenticated
templates.env.filters["local_datetime"] = local_datetime
templates.env.filters["local_date"] = local_date
templates.env.filters["utc_isoformat"] = utc_isoformat

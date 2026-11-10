from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import JSONB

JSON_TYPE = JSONB


def utcnow() -> datetime:
    return datetime.now(UTC)

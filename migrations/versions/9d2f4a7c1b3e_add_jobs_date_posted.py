"""add normalized jobs date_posted

Revision ID: 9d2f4a7c1b3e
Revises: a3b4c5d6e7f8
Create Date: 2026-11-22 08:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d2f4a7c1b3e"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _parse_date_posted(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("date_posted", sa.DateTime(timezone=True), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, raw_data ->> 'date_posted' AS date_posted "
            "FROM jobs WHERE raw_data IS NOT NULL"
        )
    ).mappings()

    updates = []
    for row in rows:
        date_posted = _parse_date_posted(row["date_posted"])
        if date_posted is not None:
            updates.append({"id": row["id"], "date_posted": date_posted})

    if updates:
        bind.execute(
            sa.text("UPDATE jobs SET date_posted = :date_posted WHERE id = :id"),
            updates,
        )


def downgrade() -> None:
    op.drop_column("jobs", "date_posted")

"""add users and job deliveries tables

Revision ID: 8f3c1a2b9d4e
Revises: 77532d848012
Create Date: 2026-11-09 12:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f3c1a2b9d4e"
down_revision: str | None = "77532d848012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "user_job_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_user_job_deliveries"),
    )
    op.create_index(
        op.f("ix_user_job_deliveries_job_id"),
        "user_job_deliveries",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_job_deliveries_user_id"),
        "user_job_deliveries",
        ["user_id"],
        unique=False,
    )

    users = sa.table(
        "users",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users,
        [{"id": 1, "name": "admin", "created_at": datetime.now(UTC)}],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_job_deliveries_user_id"),
        table_name="user_job_deliveries",
    )
    op.drop_index(
        op.f("ix_user_job_deliveries_job_id"),
        table_name="user_job_deliveries",
    )
    op.drop_table("user_job_deliveries")
    op.drop_table("users")

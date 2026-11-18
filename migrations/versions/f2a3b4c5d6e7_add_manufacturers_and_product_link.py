"""add manufacturers and product manufacturer link

Revision ID: f2a3b4c5d6e7
Revises: c4a7e2d9f1b3
Create Date: 2026-11-18 00:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "c4a7e2d9f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MANUFACTURERS = [
    ("La Sportiva", "la-sportiva"),
    ("Scarpa", "scarpa"),
    ("Five Ten", "five-ten"),
    ("Ocun", "ocun"),
    ("Tenaya", "tenaya"),
    ("Unparallel", "unparallel"),
]


def upgrade() -> None:
    op.create_table(
        "manufacturers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "name",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column(
            "slug",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("slug"),
    )

    manufacturers = sa.table(
        "manufacturers",
        sa.column("name", sqlmodel.sql.sqltypes.AutoString(length=255)),
        sa.column("slug", sqlmodel.sql.sqltypes.AutoString(length=255)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        manufacturers,
        [
            {
                "name": name,
                "slug": slug,
                "created_at": now,
                "updated_at": now,
            }
            for name, slug in MANUFACTURERS
        ],
    )

    op.add_column(
        "products",
        sa.Column("manufacturer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_products_manufacturer_id_manufacturers",
        "products",
        "manufacturers",
        ["manufacturer_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_products_manufacturer_id"),
        "products",
        ["manufacturer_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_products_manufacturer_id"),
        table_name="products",
    )
    op.drop_constraint(
        "fk_products_manufacturer_id_manufacturers",
        "products",
        type_="foreignkey",
    )
    op.drop_column("products", "manufacturer_id")
    op.drop_table("manufacturers")

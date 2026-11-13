"""add products and product sources

Revision ID: c4a7e2d9f1b3
Revises: 8f3c1a2b9d4e
Create Date: 2026-11-12 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4a7e2d9f1b3"
down_revision: str | None = "8f3c1a2b9d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "product_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "external_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=True,
        ),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "currency",
            sqlmodel.sql.sqltypes.AutoString(length=8),
            nullable=True,
        ),
        sa.Column(
            "availability",
            sqlmodel.sql.sqltypes.AutoString(length=32),
            nullable=True,
        ),
        sa.Column(
            "sizes",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "raw_data",
            sa.JSON().with_variant(
                postgresql.JSONB(astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "url",
            name="uq_product_sources_source_url",
        ),
    )
    op.create_index(
        op.f("ix_product_sources_product_id"),
        "product_sources",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_product_sources_source_id"),
        "product_sources",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_product_sources_source_id"),
        table_name="product_sources",
    )
    op.drop_index(
        op.f("ix_product_sources_product_id"),
        table_name="product_sources",
    )
    op.drop_table("product_sources")
    op.drop_table("products")

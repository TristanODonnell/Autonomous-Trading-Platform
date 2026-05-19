"""add raw_market_symbols, raw_market_pool_snapshots, raw_market_pool_memberships tables

Revision ID: mm90nn12oo34
Revises: ll89mm01nn23
Create Date: 2026-05-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "mm90nn12oo34"
down_revision: str | Sequence[str] | None = "ll89mm01nn23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)

    if "raw_market_symbols" not in existing_tables:
        op.create_table(
            "raw_market_symbols",
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=True),
            sa.Column("asset_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("is_tradable", sa.Boolean(), nullable=False),
            sa.Column("name", sa.String(length=256), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=True),
            sa.Column("country", sa.String(length=8), nullable=True),
            sa.Column("marginable", sa.Boolean(), nullable=True),
            sa.Column("shortable", sa.Boolean(), nullable=True),
            sa.Column("fractionable", sa.Boolean(), nullable=True),
            sa.Column("easy_to_borrow", sa.Boolean(), nullable=True),
            sa.Column("provider_symbol", sa.String(length=64), nullable=True),
            sa.Column("provider_asset_id", sa.String(length=128), nullable=True),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "metadata_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("symbol", name=op.f("pk_raw_market_symbols")),
        )
        op.create_index(
            op.f("ix_raw_market_symbols_asset_type"), "raw_market_symbols", ["asset_type"]
        )
        op.create_index(op.f("ix_raw_market_symbols_status"), "raw_market_symbols", ["status"])
        op.create_index(
            op.f("ix_raw_market_symbols_is_tradable"), "raw_market_symbols", ["is_tradable"]
        )
        op.create_index(op.f("ix_raw_market_symbols_exchange"), "raw_market_symbols", ["exchange"])
        op.create_index(
            op.f("ix_raw_market_symbols_last_seen"), "raw_market_symbols", ["last_seen"]
        )

    if "raw_market_pool_snapshots" not in existing_tables:
        op.create_table(
            "raw_market_pool_snapshots",
            sa.Column("snapshot_id", sa.String(length=64), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("symbol_count", sa.Integer(), nullable=False),
            sa.Column("cadence", sa.String(length=16), nullable=False),
            sa.Column("is_complete", sa.Boolean(), nullable=False),
            sa.Column(
                "metadata_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_raw_market_pool_snapshots")),
        )
        op.create_index(
            op.f("ix_raw_market_pool_snapshots_captured_at"),
            "raw_market_pool_snapshots",
            ["captured_at"],
        )
        op.create_index(
            op.f("ix_raw_market_pool_snapshots_source"),
            "raw_market_pool_snapshots",
            ["source"],
        )
        op.create_index(
            op.f("ix_raw_market_pool_snapshots_is_complete"),
            "raw_market_pool_snapshots",
            ["is_complete"],
        )

    if "raw_market_pool_memberships" not in existing_tables:
        op.create_table(
            "raw_market_pool_memberships",
            sa.Column("snapshot_id", sa.String(length=64), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("is_tradable", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("asset_type", sa.String(length=32), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=True),
            sa.ForeignKeyConstraint(
                ["snapshot_id"],
                ["raw_market_pool_snapshots.snapshot_id"],
                name=op.f("fk_raw_market_pool_memberships_snapshot_id_raw_market_pool_snapshots"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "snapshot_id", "symbol", name=op.f("pk_raw_market_pool_memberships")
            ),
        )
        op.create_index(
            op.f("ix_raw_market_pool_memberships_snapshot_id"),
            "raw_market_pool_memberships",
            ["snapshot_id"],
        )
        op.create_index(
            op.f("ix_raw_market_pool_memberships_symbol"),
            "raw_market_pool_memberships",
            ["symbol"],
        )
        op.create_index(
            op.f("ix_raw_market_pool_memberships_asset_type"),
            "raw_market_pool_memberships",
            ["asset_type"],
        )
        op.create_index(
            op.f("ix_raw_market_pool_memberships_is_tradable"),
            "raw_market_pool_memberships",
            ["is_tradable"],
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_raw_market_pool_memberships_is_tradable"),
        table_name="raw_market_pool_memberships",
    )
    op.drop_index(
        op.f("ix_raw_market_pool_memberships_asset_type"),
        table_name="raw_market_pool_memberships",
    )
    op.drop_index(
        op.f("ix_raw_market_pool_memberships_symbol"),
        table_name="raw_market_pool_memberships",
    )
    op.drop_index(
        op.f("ix_raw_market_pool_memberships_snapshot_id"),
        table_name="raw_market_pool_memberships",
    )
    op.drop_table("raw_market_pool_memberships")

    op.drop_index(
        op.f("ix_raw_market_pool_snapshots_is_complete"),
        table_name="raw_market_pool_snapshots",
    )
    op.drop_index(
        op.f("ix_raw_market_pool_snapshots_source"), table_name="raw_market_pool_snapshots"
    )
    op.drop_index(
        op.f("ix_raw_market_pool_snapshots_captured_at"),
        table_name="raw_market_pool_snapshots",
    )
    op.drop_table("raw_market_pool_snapshots")

    op.drop_index(op.f("ix_raw_market_symbols_last_seen"), table_name="raw_market_symbols")
    op.drop_index(op.f("ix_raw_market_symbols_exchange"), table_name="raw_market_symbols")
    op.drop_index(op.f("ix_raw_market_symbols_is_tradable"), table_name="raw_market_symbols")
    op.drop_index(op.f("ix_raw_market_symbols_status"), table_name="raw_market_symbols")
    op.drop_index(op.f("ix_raw_market_symbols_asset_type"), table_name="raw_market_symbols")
    op.drop_table("raw_market_symbols")

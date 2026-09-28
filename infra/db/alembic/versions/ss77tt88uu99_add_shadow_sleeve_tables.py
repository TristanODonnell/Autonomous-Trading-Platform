"""add shadow sleeve tables

On-deck shadow book (portfolio rotation step 2A). Same columns as the real sleeve
tables, kept separate so shadow positions can never enter the sleeve/account
invariant, internal crossing or adoption:

  shadow_sleeve_positions  current simulated holding per (strategy, symbol)
  shadow_sleeve_ledger     append-only simulated fills / tier-exit liquidations
  shadow_sleeve_snapshots  point-in-time valuation + orders blocked by risk/throttle

Revision ID: ss77tt88uu99
Revises: rr66ss77tt88
Create Date: 2026-09-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ss77tt88uu99"
down_revision = "rr66ss77tt88"
branch_labels = None
depends_on = None

_MONEY = sa.Numeric(18, 6)
_QUANTITY = sa.Numeric(18, 9)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "shadow_sleeve_positions",
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", _QUANTITY, nullable=False),
        sa.Column("avg_cost", _MONEY, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("strategy_id", "symbol", name="pk_shadow_sleeve_positions"),
    )
    op.create_index("ix_shsp_symbol", "shadow_sleeve_positions", ["symbol"])

    op.create_table(
        "shadow_sleeve_ledger",
        sa.Column("entry_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", _QUANTITY, nullable=False),
        sa.Column("price", _MONEY, nullable=False),
        sa.Column("fees", _MONEY, nullable=False),
        sa.Column("realized_pnl", _MONEY, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("fill_id", sa.String(64), nullable=True),
        sa.Column("cross_id", sa.String(64), nullable=True),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("timestamp", _TS, nullable=False),
        sa.PrimaryKeyConstraint("entry_id", name="pk_shadow_sleeve_ledger"),
    )
    op.create_index(
        "ix_shsl_strategy_timestamp", "shadow_sleeve_ledger", ["strategy_id", "timestamp"]
    )

    op.create_table(
        "shadow_sleeve_snapshots",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("timestamp", _TS, nullable=False),
        sa.Column("allocated_capital", _MONEY, nullable=True),
        sa.Column("market_value", _MONEY, nullable=False),
        sa.Column("cost_basis", _MONEY, nullable=False),
        sa.Column("realized_pnl", _MONEY, nullable=False),
        sa.Column("unrealized_pnl", _MONEY, nullable=False),
        sa.Column("fees", _MONEY, nullable=False),
        sa.Column("net_pnl", _MONEY, nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("unpriced_symbols", postgresql.JSONB(), nullable=True),
        sa.Column("blocked_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_shadow_sleeve_snapshots"),
    )
    op.create_index(
        "ix_shss_strategy_timestamp", "shadow_sleeve_snapshots", ["strategy_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_shss_strategy_timestamp", table_name="shadow_sleeve_snapshots")
    op.drop_table("shadow_sleeve_snapshots")
    op.drop_index("ix_shsl_strategy_timestamp", table_name="shadow_sleeve_ledger")
    op.drop_table("shadow_sleeve_ledger")
    op.drop_index("ix_shsp_symbol", table_name="shadow_sleeve_positions")
    op.drop_table("shadow_sleeve_positions")

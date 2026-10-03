"""add strategy sleeve tables

Per-strategy books inside the shared broker account (portfolio rotation step 1B):

  strategy_sleeve_positions  current holding per (strategy, symbol); row deleted when flat
  strategy_sleeve_ledger     append-only fills / internal crosses / adoptions
  strategy_sleeve_snapshots  point-in-time valuation with cumulative P&L

Revision ID: qq55rr66ss77
Revises: pp44qq55rr66
Create Date: 2026-09-26

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qq55rr66ss77"
down_revision = "pp44qq55rr66"
branch_labels = None
depends_on = None

_MONEY = sa.Numeric(18, 6)
_QUANTITY = sa.Numeric(18, 9)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "strategy_sleeve_positions",
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", _QUANTITY, nullable=False),
        sa.Column("avg_cost", _MONEY, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("strategy_id", "symbol", name="pk_strategy_sleeve_positions"),
    )
    op.create_index("ix_ssp_symbol", "strategy_sleeve_positions", ["symbol"])

    op.create_table(
        "strategy_sleeve_ledger",
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
        sa.PrimaryKeyConstraint("entry_id", name="pk_strategy_sleeve_ledger"),
    )
    op.create_index(
        "ix_ssl_strategy_timestamp", "strategy_sleeve_ledger", ["strategy_id", "timestamp"]
    )
    op.create_index("ix_ssl_fill_id", "strategy_sleeve_ledger", ["fill_id"])

    op.create_table(
        "strategy_sleeve_snapshots",
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
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_strategy_sleeve_snapshots"),
    )
    op.create_index(
        "ix_sss_strategy_timestamp", "strategy_sleeve_snapshots", ["strategy_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_sss_strategy_timestamp", table_name="strategy_sleeve_snapshots")
    op.drop_table("strategy_sleeve_snapshots")
    op.drop_index("ix_ssl_fill_id", table_name="strategy_sleeve_ledger")
    op.drop_index("ix_ssl_strategy_timestamp", table_name="strategy_sleeve_ledger")
    op.drop_table("strategy_sleeve_ledger")
    op.drop_index("ix_ssp_symbol", table_name="strategy_sleeve_positions")
    op.drop_table("strategy_sleeve_positions")

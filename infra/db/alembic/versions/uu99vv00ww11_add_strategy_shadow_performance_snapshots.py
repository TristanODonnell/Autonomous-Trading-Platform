"""add strategy_shadow_performance_snapshots

On-deck shadow metrics (portfolio rotation step 2D). Same columns as
strategy_live_performance_snapshots, in a separate table so shadow evidence is never
read as live by health, correlation or risk budgeting.

Revision ID: uu99vv00ww11
Revises: tt88uu99vv00
Create Date: 2026-09-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "uu99vv00ww11"
down_revision = "tt88uu99vv00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_shadow_performance_snapshots",
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("window_trades", sa.Integer(), nullable=True),
        sa.Column("realized_return", sa.Float(), nullable=True),
        sa.Column("rolling_sharpe", sa.Float(), nullable=True),
        sa.Column("realized_drawdown", sa.Float(), nullable=True),
        sa.Column("realized_volatility", sa.Float(), nullable=True),
        sa.Column("live_win_rate", sa.Float(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("winning_trade_count", sa.Integer(), nullable=True),
        sa.Column("days_live", sa.Integer(), nullable=True),
        sa.Column("days_since_profitable_day", sa.Integer(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("metric_lineage_type", sa.String(32), nullable=True),
        sa.Column("environment", sa.String(64), nullable=True),
        sa.Column("calculation_version", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_strategy_shadow_performance_snapshots"),
    )
    op.create_index(
        "ix_shps_strategy_computed_at",
        "strategy_shadow_performance_snapshots",
        ["strategy_id", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shps_strategy_computed_at", table_name="strategy_shadow_performance_snapshots"
    )
    op.drop_table("strategy_shadow_performance_snapshots")

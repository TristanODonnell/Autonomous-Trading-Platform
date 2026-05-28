"""Add strategy_live_performance_snapshots table

Persists rolling live/runtime performance metrics computed from fills and
cash_snapshots.  Used by QualityBasedReallocationService for blended
quality scoring (live metrics + backtest metrics weighted by live history
depth) and by AutoPromotionService for transparency.

Revision ID: ccdd33ee44ff
Revises: bbcc22dd33ee
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ccdd33ee44ff"
down_revision: str | Sequence[str] | None = "bbcc22dd33ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind)
    if "strategy_live_performance_snapshots" in existing_tables:
        return

    op.create_table(
        "strategy_live_performance_snapshots",
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
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index(
        "ix_slps_strategy_id_computed_at",
        "strategy_live_performance_snapshots",
        ["strategy_id", "computed_at"],
    )
    op.create_index(
        "ix_slps_computed_at",
        "strategy_live_performance_snapshots",
        ["computed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_slps_computed_at",
        table_name="strategy_live_performance_snapshots",
    )
    op.drop_index(
        "ix_slps_strategy_id_computed_at",
        table_name="strategy_live_performance_snapshots",
    )
    op.drop_table("strategy_live_performance_snapshots")

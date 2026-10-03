"""add bench management settings and bench_evaluations

Portfolio rotation step 3B:

  operator_settings  bench_management_enabled (default off), max_bench_strategies,
                     bench_correlation_threshold, bench_resim_window_days,
                     bench_score_floor, bench_floor_strikes, bench_max_idle_days
  bench_evaluations  one row per (bench review, strategy): re-sim metrics, correlation
                     group, decision and reason

Revision ID: vv00ww11xx22
Revises: uu99vv00ww11
Create Date: 2026-09-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "vv00ww11xx22"
down_revision = "uu99vv00ww11"
branch_labels = None
depends_on = None

_SETTINGS_COLUMNS = [
    sa.Column("bench_management_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("max_bench_strategies", sa.Integer(), nullable=False, server_default="25"),
    sa.Column(
        "bench_correlation_threshold", sa.Numeric(6, 4), nullable=False, server_default="0.85"
    ),
    sa.Column("bench_resim_window_days", sa.Integer(), nullable=False, server_default="63"),
    sa.Column("bench_score_floor", sa.Numeric(8, 4), nullable=False, server_default="1.0"),
    sa.Column("bench_floor_strikes", sa.Integer(), nullable=False, server_default="3"),
    sa.Column("bench_max_idle_days", sa.Integer(), nullable=False, server_default="120"),
]


def upgrade() -> None:
    for column in _SETTINGS_COLUMNS:
        op.add_column("operator_settings", column)

    op.create_table(
        "bench_evaluations",
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("strategy_type", sa.String(64), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("resim_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("total_return", sa.Float(), nullable=True),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("score", sa.Numeric(12, 6), nullable=True),
        sa.Column("group_id", sa.String(64), nullable=True),
        sa.Column("is_champion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_correlation", sa.Float(), nullable=True),
        sa.Column("correlated_with", sa.String(128), nullable=True),
        sa.Column("floor_strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("evaluation_id", name="pk_bench_evaluations"),
    )
    op.create_index(
        "ix_bench_eval_strategy_reviewed", "bench_evaluations", ["strategy_id", "reviewed_at"]
    )
    op.create_index("ix_bench_eval_review", "bench_evaluations", ["review_id"])


def downgrade() -> None:
    op.drop_index("ix_bench_eval_review", table_name="bench_evaluations")
    op.drop_index("ix_bench_eval_strategy_reviewed", table_name="bench_evaluations")
    op.drop_table("bench_evaluations")
    for column in reversed(_SETTINGS_COLUMNS):
        op.drop_column("operator_settings", column.name)

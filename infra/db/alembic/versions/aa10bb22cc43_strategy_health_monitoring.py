"""add strategy health monitoring tables (FINDING-09)

Revision ID: aa10bb22cc43
Revises: zz09aa21bb32
Create Date: 2026-05-27 00:00:00.000000

Adds two tables:
  - strategy_quality_score_history: persists per-strategy quality scores after
    each rebalance cycle, enabling trend analysis over time.
  - strategy_health_states: persists current health status (healthy/watch/degrading/
    critical) per strategy, separate from governance state so an approved_live strategy
    can be flagged as degrading without triggering a governance transition.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aa10bb22cc43"
down_revision = "zz09aa21bb32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_quality_score_history",
        sa.Column("score_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("rebalance_run_id", sa.String(64), nullable=True),
        sa.Column("quality_score", sa.Float, nullable=False),
        sa.Column("live_score", sa.Float, nullable=True),
        sa.Column("backtest_score", sa.Float, nullable=True),
        sa.Column("blended_score", sa.Float, nullable=True),
        sa.Column("alpha_weight", sa.Float, nullable=True),
        sa.Column("score_components", sa.JSON, nullable=True),
        sa.Column("data_window", sa.JSON, nullable=True),
        sa.Column("governance_state", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sqsh_strategy_id_computed_at",
        "strategy_quality_score_history",
        ["strategy_id", "computed_at"],
    )
    op.create_index(
        "ix_sqsh_rebalance_run_id",
        "strategy_quality_score_history",
        ["rebalance_run_id"],
    )

    op.create_table(
        "strategy_health_states",
        sa.Column("health_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("health_status", sa.String(32), nullable=False),
        sa.Column("previous_health_status", sa.String(32), nullable=True),
        sa.Column("health_reason", sa.String(512), nullable=True),
        sa.Column("consecutive_decline_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_score_trend", sa.String(32), nullable=True),
        sa.Column("latest_quality_score", sa.Float, nullable=True),
        sa.Column("realized_drawdown", sa.Float, nullable=True),
        sa.Column("last_health_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_rebalance_run_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("strategy_id", name="uq_strategy_health_states_strategy_id"),
    )
    op.create_index("ix_shs_strategy_id", "strategy_health_states", ["strategy_id"])
    op.create_index("ix_shs_health_status", "strategy_health_states", ["health_status"])


def downgrade() -> None:
    op.drop_index("ix_shs_health_status", table_name="strategy_health_states")
    op.drop_index("ix_shs_strategy_id", table_name="strategy_health_states")
    op.drop_table("strategy_health_states")

    op.drop_index("ix_sqsh_rebalance_run_id", table_name="strategy_quality_score_history")
    op.drop_index("ix_sqsh_strategy_id_computed_at", table_name="strategy_quality_score_history")
    op.drop_table("strategy_quality_score_history")

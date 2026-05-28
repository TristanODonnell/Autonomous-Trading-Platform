"""add risk budget snapshots table (TASK-5.2)

Revision ID: cc22dd34ee55
Revises: bb11cc23dd44
Create Date: 2026-05-27 00:00:00.000000

Adds risk_budget_snapshots: persists the output of each risk budgeting
computation run including recommended capital weights, realized risk
contributions per strategy, portfolio volatility estimate, diversification
ratio, covariance snapshot reference (from TASK-5.1), and fallback metadata.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cc22dd34ee55"
down_revision = "bb11cc23dd44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_budget_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("strategy_universe", sa.JSON, nullable=False),
        sa.Column("strategy_count", sa.Integer, nullable=False),
        sa.Column("target_risk_budgets", sa.JSON, nullable=False),
        sa.Column("recommended_weights", sa.JSON, nullable=False),
        sa.Column("realized_risk_contributions", sa.JSON, nullable=False),
        sa.Column("portfolio_volatility_estimate", sa.Float, nullable=True),
        sa.Column("diversification_ratio", sa.Float, nullable=True),
        sa.Column("covariance_snapshot_id", sa.String(64), nullable=True),
        sa.Column("covariance_lookback_window", sa.Integer, nullable=True),
        sa.Column("fallback_used", sa.Boolean, nullable=False),
        sa.Column("fallback_reason", sa.String(64), nullable=True),
        sa.Column("convergence_achieved", sa.Boolean, nullable=False),
        sa.Column("iterations_to_convergence", sa.Integer, nullable=True),
        sa.Column("hidden_concentration_detected", sa.Boolean, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_rbs_computed_at", "risk_budget_snapshots", ["computed_at"])
    op.create_index("ix_rbs_mode", "risk_budget_snapshots", ["mode"])
    op.create_index("ix_rbs_run_id", "risk_budget_snapshots", ["run_id"])
    op.create_index(
        "ix_rbs_covariance_snapshot_id",
        "risk_budget_snapshots",
        ["covariance_snapshot_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rbs_covariance_snapshot_id", table_name="risk_budget_snapshots")
    op.drop_index("ix_rbs_run_id", table_name="risk_budget_snapshots")
    op.drop_index("ix_rbs_mode", table_name="risk_budget_snapshots")
    op.drop_index("ix_rbs_computed_at", table_name="risk_budget_snapshots")
    op.drop_table("risk_budget_snapshots")

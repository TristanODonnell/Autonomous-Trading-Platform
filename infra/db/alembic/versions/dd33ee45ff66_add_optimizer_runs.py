"""add optimizer_runs table (TASK-5.3)

Revision ID: dd33ee45ff66
Revises: cc22dd34ee55
Create Date: 2026-05-27 00:00:00.000000

Adds optimizer_runs: persists each mean-variance optimizer invocation with
full input/output metadata — covariance snapshot reference, expected-return
source, constraints applied, solver status, target weights, and fallback
metadata.  Supports shadow-mode comparison, audit trails, and downstream
optimizer debugging.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dd33ee45ff66"
down_revision = "cc22dd34ee55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optimizer_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("objective", sa.String(64), nullable=False),
        sa.Column("solver_status", sa.String(32), nullable=False),
        sa.Column("fallback_mode", sa.String(32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dry_run", sa.Boolean, nullable=False),
        sa.Column("asset_universe", sa.JSON, nullable=False),
        sa.Column("asset_count", sa.Integer, nullable=False),
        sa.Column("target_weights", sa.JSON, nullable=False),
        sa.Column("current_weights", sa.JSON, nullable=False),
        sa.Column("expected_return", sa.Float, nullable=True),
        sa.Column("expected_volatility", sa.Float, nullable=True),
        sa.Column("objective_value", sa.Float, nullable=True),
        sa.Column("realized_turnover", sa.Float, nullable=True),
        sa.Column("risk_aversion", sa.Float, nullable=False),
        sa.Column("covariance_snapshot_id", sa.String(64), nullable=True),
        sa.Column("expected_return_source", sa.String(128), nullable=False),
        sa.Column("constraints_applied", sa.JSON, nullable=False),
        sa.Column("binding_constraints", sa.JSON, nullable=False),
        sa.Column("infeasibility_reason", sa.String(512), nullable=True),
        sa.Column("convergence_achieved", sa.Boolean, nullable=False),
        sa.Column("iterations_to_convergence", sa.Integer, nullable=True),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_opt_generated_at", "optimizer_runs", ["generated_at"])
    op.create_index("ix_opt_solver_status", "optimizer_runs", ["solver_status"])
    op.create_index("ix_opt_dry_run", "optimizer_runs", ["dry_run"])
    op.create_index("ix_opt_covariance_snapshot_id", "optimizer_runs", ["covariance_snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_opt_covariance_snapshot_id", table_name="optimizer_runs")
    op.drop_index("ix_opt_dry_run", table_name="optimizer_runs")
    op.drop_index("ix_opt_solver_status", table_name="optimizer_runs")
    op.drop_index("ix_opt_generated_at", table_name="optimizer_runs")
    op.drop_table("optimizer_runs")

"""add factor neutralization runs

Revision ID: bbb10cc22dd55
Revises: aaa10bb22cc54
Create Date: 2026-05-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bbb10cc22dd55"
down_revision = "aaa10bb22cc54"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_neutralization_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=True),
        sa.Column("factor_snapshot_id", sa.String(64), nullable=True),
        sa.Column("covariance_snapshot_id", sa.String(64), nullable=True),
        sa.Column("optimization_run_id", sa.String(64), nullable=True),
        sa.Column("config_json", sa.JSON, nullable=False),
        sa.Column("original_weights", sa.JSON, nullable=False),
        sa.Column("target_weights", sa.JSON, nullable=False),
        sa.Column("pre_exposures", sa.JSON, nullable=False),
        sa.Column("post_exposures", sa.JSON, nullable=False),
        sa.Column("exposure_reduction", sa.JSON, nullable=False),
        sa.Column("residual_exposure", sa.JSON, nullable=False),
        sa.Column("constraint_utilization", sa.JSON, nullable=False),
        sa.Column("binding_constraints", sa.JSON, nullable=False),
        sa.Column("constraint_violations", sa.JSON, nullable=False),
        sa.Column("fallback_mode", sa.String(64), nullable=True),
        sa.Column("infeasibility_reason", sa.String(512), nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_factor_neut_generated_at", "factor_neutralization_runs", ["generated_at"])
    op.create_index("ix_factor_neut_status", "factor_neutralization_runs", ["status"])
    op.create_index("ix_factor_neut_mode", "factor_neutralization_runs", ["mode"])
    op.create_index("ix_factor_neut_portfolio_id", "factor_neutralization_runs", ["portfolio_id"])
    op.create_index(
        "ix_factor_neut_factor_snapshot_id",
        "factor_neutralization_runs",
        ["factor_snapshot_id"],
    )
    op.create_index(
        "ix_factor_neut_optimization_run_id",
        "factor_neutralization_runs",
        ["optimization_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_factor_neut_optimization_run_id", table_name="factor_neutralization_runs")
    op.drop_index("ix_factor_neut_factor_snapshot_id", table_name="factor_neutralization_runs")
    op.drop_index("ix_factor_neut_portfolio_id", table_name="factor_neutralization_runs")
    op.drop_index("ix_factor_neut_mode", table_name="factor_neutralization_runs")
    op.drop_index("ix_factor_neut_status", table_name="factor_neutralization_runs")
    op.drop_index("ix_factor_neut_generated_at", table_name="factor_neutralization_runs")
    op.drop_table("factor_neutralization_runs")

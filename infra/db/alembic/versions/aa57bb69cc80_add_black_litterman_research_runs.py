"""add black-litterman research artifact table

Revision ID: aa57bb69cc80
Revises: zz09aa21bb32
Create Date: 2026-05-27 00:00:00.000000

Persists research-only Black-Litterman allocation artifacts with priors,
views, posterior returns, proposed dry-run weights, and deterministic hashes
for reproducibility and audit comparison.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aa57bb69cc80"
down_revision = "zz09aa21bb32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "black_litterman_research_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("universe_id", sa.String(128), nullable=True),
        sa.Column("universe", sa.JSON, nullable=False),
        sa.Column("universe_size", sa.Integer, nullable=False),
        sa.Column("covariance_snapshot_id", sa.String(64), nullable=True),
        sa.Column("covariance_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("benchmark_weights", sa.JSON, nullable=False),
        sa.Column("tau", sa.Float, nullable=False),
        sa.Column("risk_aversion", sa.Float, nullable=False),
        sa.Column("views", sa.JSON, nullable=False),
        sa.Column("confidences", sa.JSON, nullable=False),
        sa.Column("prior_returns", sa.JSON, nullable=False),
        sa.Column("posterior_returns", sa.JSON, nullable=False),
        sa.Column("posterior_covariance", sa.JSON, nullable=True),
        sa.Column("proposed_weights", sa.JSON, nullable=False),
        sa.Column("constraints_used", sa.JSON, nullable=False),
        sa.Column("diagnostics", sa.JSON, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("views_hash", sa.String(64), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("optimizer_status", sa.String(64), nullable=True),
        sa.Column("metadata_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_blr_generated_at", "black_litterman_research_runs", ["generated_at"])
    op.create_index(
        "ix_blr_covariance_snapshot_id",
        "black_litterman_research_runs",
        ["covariance_snapshot_id"],
    )
    op.create_index("ix_blr_input_hash", "black_litterman_research_runs", ["input_hash"])
    op.create_index("ix_blr_artifact_hash", "black_litterman_research_runs", ["artifact_hash"])


def downgrade() -> None:
    op.drop_index("ix_blr_artifact_hash", table_name="black_litterman_research_runs")
    op.drop_index("ix_blr_input_hash", table_name="black_litterman_research_runs")
    op.drop_index("ix_blr_covariance_snapshot_id", table_name="black_litterman_research_runs")
    op.drop_index("ix_blr_generated_at", table_name="black_litterman_research_runs")
    op.drop_table("black_litterman_research_runs")

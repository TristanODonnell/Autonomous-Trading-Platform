"""add correlation and covariance snapshot tables (TASK-5.1)

Revision ID: bb11cc23dd44
Revises: aa10bb22cc43
Create Date: 2026-05-27 00:00:00.000000

Adds two tables:
  - correlation_snapshots: persists rolling pairwise correlation matrices
    (symbol, strategy, sector) with diagnostics, top correlated pairs,
    cluster metadata, and numerical stability indicators.
  - covariance_snapshots: persists rolling annualised covariance matrices
    with positive-definite check, condition number, and a deterministic
    matrix hash for downstream optimizer consumption.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bb11cc23dd44"
down_revision = "aa10bb22cc43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "correlation_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_type", sa.String(32), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_universe", sa.JSON, nullable=False),
        sa.Column("asset_count", sa.Integer, nullable=False),
        sa.Column("observations_used", sa.Integer, nullable=False),
        sa.Column("average_pairwise_correlation", sa.Float, nullable=False),
        sa.Column("max_pairwise_correlation", sa.Float, nullable=False),
        sa.Column("min_pairwise_correlation", sa.Float, nullable=False),
        sa.Column("correlation_matrix", sa.JSON, nullable=False),
        sa.Column("top_correlated_pairs", sa.JSON, nullable=False),
        sa.Column("clusters", sa.JSON, nullable=False),
        sa.Column("cluster_count", sa.Integer, nullable=False),
        sa.Column("is_numerically_stable", sa.Boolean, nullable=False),
        sa.Column("condition_number", sa.Float, nullable=True),
        sa.Column("regime_label", sa.String(64), nullable=True),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("computation_duration_seconds", sa.Float, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_corr_snap_type_window_as_of",
        "correlation_snapshots",
        ["snapshot_type", "lookback_window", "as_of_date"],
    )
    op.create_index(
        "ix_corr_snap_computed_at",
        "correlation_snapshots",
        ["computed_at"],
    )
    op.create_index(
        "ix_corr_snap_run_id",
        "correlation_snapshots",
        ["run_id"],
    )

    op.create_table(
        "covariance_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_type", sa.String(32), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asset_universe", sa.JSON, nullable=False),
        sa.Column("asset_count", sa.Integer, nullable=False),
        sa.Column("observations_used", sa.Integer, nullable=False),
        sa.Column("annualization_factor", sa.Integer, nullable=False),
        sa.Column("covariance_matrix", sa.JSON, nullable=False),
        sa.Column("matrix_hash", sa.String(64), nullable=True),
        sa.Column("is_positive_definite", sa.Boolean, nullable=False),
        sa.Column("condition_number", sa.Float, nullable=True),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("computation_duration_seconds", sa.Float, nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_cov_snap_type_window_as_of",
        "covariance_snapshots",
        ["snapshot_type", "lookback_window", "as_of_date"],
    )
    op.create_index(
        "ix_cov_snap_computed_at",
        "covariance_snapshots",
        ["computed_at"],
    )
    op.create_index(
        "ix_cov_snap_run_id",
        "covariance_snapshots",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cov_snap_run_id", table_name="covariance_snapshots")
    op.drop_index("ix_cov_snap_computed_at", table_name="covariance_snapshots")
    op.drop_index("ix_cov_snap_type_window_as_of", table_name="covariance_snapshots")
    op.drop_table("covariance_snapshots")

    op.drop_index("ix_corr_snap_run_id", table_name="correlation_snapshots")
    op.drop_index("ix_corr_snap_computed_at", table_name="correlation_snapshots")
    op.drop_index("ix_corr_snap_type_window_as_of", table_name="correlation_snapshots")
    op.drop_table("correlation_snapshots")

"""add factor exposure monitoring snapshots

Revision ID: aaa10bb22cc54
Revises: zz09aa21bb32
Create Date: 2026-05-27 00:00:00.000000

Persists portfolio, strategy, and symbol factor exposure diagnostics for
benchmark-relative beta, momentum, realized volatility, sector concentration,
and metadata-backed size/quality/value factors.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "aaa10bb22cc54"
down_revision = "zz09aa21bb32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_exposure_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("portfolio_id", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("benchmark_symbol", sa.String(32), nullable=False),
        sa.Column("benchmark_source", sa.String(64), nullable=False),
        sa.Column("factor_computation_version", sa.String(32), nullable=False),
        sa.Column("portfolio_exposures", sa.JSON, nullable=False),
        sa.Column("strategy_exposures", sa.JSON, nullable=False),
        sa.Column("symbol_exposures", sa.JSON, nullable=False),
        sa.Column("sector_exposures", sa.JSON, nullable=False),
        sa.Column("concentration_diagnostics", sa.JSON, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("factor_methodology", sa.JSON, nullable=False),
        sa.Column("data_lineage", sa.JSON, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
    )
    op.create_index(
        "ix_factor_snap_portfolio_window_as_of",
        "factor_exposure_snapshots",
        ["portfolio_id", "lookback_window", "as_of_date"],
    )
    op.create_index("ix_factor_snap_computed_at", "factor_exposure_snapshots", ["computed_at"])
    op.create_index("ix_factor_snap_run_id", "factor_exposure_snapshots", ["run_id"])
    op.create_index("ix_factor_snap_benchmark", "factor_exposure_snapshots", ["benchmark_symbol"])

    op.create_table(
        "strategy_factor_exposures",
        sa.Column("exposure_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("portfolio_id", sa.String(64), nullable=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("benchmark_symbol", sa.String(32), nullable=False),
        sa.Column("strategy_weight", sa.Float, nullable=False),
        sa.Column("symbol_count", sa.Integer, nullable=False),
        sa.Column("exposures", sa.JSON, nullable=False),
        sa.Column("top_symbol_contributors", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_strategy_factor_strategy_window_as_of",
        "strategy_factor_exposures",
        ["strategy_id", "lookback_window", "as_of_date"],
    )
    op.create_index("ix_strategy_factor_snapshot_id", "strategy_factor_exposures", ["snapshot_id"])
    op.create_index("ix_strategy_factor_run_id", "strategy_factor_exposures", ["run_id"])

    op.create_table(
        "portfolio_factor_exposures",
        sa.Column("exposure_id", sa.String(64), primary_key=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("portfolio_id", sa.String(64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lookback_window", sa.Integer, nullable=False),
        sa.Column("benchmark_symbol", sa.String(32), nullable=False),
        sa.Column("total_weight", sa.Float, nullable=False),
        sa.Column("symbol_count", sa.Integer, nullable=False),
        sa.Column("strategy_count", sa.Integer, nullable=False),
        sa.Column("exposures", sa.JSON, nullable=False),
        sa.Column("sector_exposures", sa.JSON, nullable=False),
        sa.Column("concentration_diagnostics", sa.JSON, nullable=False),
    )
    op.create_index(
        "ix_portfolio_factor_portfolio_window_as_of",
        "portfolio_factor_exposures",
        ["portfolio_id", "lookback_window", "as_of_date"],
    )
    op.create_index(
        "ix_portfolio_factor_snapshot_id", "portfolio_factor_exposures", ["snapshot_id"]
    )
    op.create_index("ix_portfolio_factor_run_id", "portfolio_factor_exposures", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_factor_run_id", table_name="portfolio_factor_exposures")
    op.drop_index("ix_portfolio_factor_snapshot_id", table_name="portfolio_factor_exposures")
    op.drop_index(
        "ix_portfolio_factor_portfolio_window_as_of",
        table_name="portfolio_factor_exposures",
    )
    op.drop_table("portfolio_factor_exposures")

    op.drop_index("ix_strategy_factor_run_id", table_name="strategy_factor_exposures")
    op.drop_index("ix_strategy_factor_snapshot_id", table_name="strategy_factor_exposures")
    op.drop_index(
        "ix_strategy_factor_strategy_window_as_of",
        table_name="strategy_factor_exposures",
    )
    op.drop_table("strategy_factor_exposures")

    op.drop_index("ix_factor_snap_benchmark", table_name="factor_exposure_snapshots")
    op.drop_index("ix_factor_snap_run_id", table_name="factor_exposure_snapshots")
    op.drop_index("ix_factor_snap_computed_at", table_name="factor_exposure_snapshots")
    op.drop_index(
        "ix_factor_snap_portfolio_window_as_of",
        table_name="factor_exposure_snapshots",
    )
    op.drop_table("factor_exposure_snapshots")

"""Add metric lineage fields and blended_metrics_snapshots table

Revision ID: dd44ee55ff66
Revises: zz09aa21bb32
Create Date: 2026-05-27 00:00:00.000000

Design note:
Implements Recommendation 6.2 — Separate Live Metrics from Research Metrics.

Changes:
  1. metrics_summary: add metric_lineage_type, environment, calculation_version,
     lookback_window_days, source_strategy_ids (nullable, backward compatible).
  2. strategy_live_performance_snapshots: add metric_lineage_type, environment,
     calculation_version (nullable, backward compatible).
  3. blended_metrics_snapshots: new table storing confidence-weighted blended
     metric snapshots with full alpha/score provenance.

All new columns on existing tables are nullable so existing rows remain valid
without a data migration.  New writes populate these columns with the lineage
type corresponding to their source domain.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "dd44ee55ff66"
down_revision = ("zz09aa21bb32", "ccdd33ee44ff")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. metrics_summary — add lineage columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("metrics_summary") as batch_op:
        batch_op.add_column(sa.Column("metric_lineage_type", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("environment", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("calculation_version", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("lookback_window_days", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "source_strategy_ids",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            )
        )

    # ------------------------------------------------------------------
    # 2. strategy_live_performance_snapshots — add lineage columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("strategy_live_performance_snapshots") as batch_op:
        batch_op.add_column(sa.Column("metric_lineage_type", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("environment", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("calculation_version", sa.String(32), nullable=True))

    # ------------------------------------------------------------------
    # 3. blended_metrics_snapshots — new table
    # ------------------------------------------------------------------
    op.create_table(
        "blended_metrics_snapshots",
        sa.Column("blended_snapshot_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("rebalance_run_id", sa.String(64), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "metric_lineage_type",
            sa.String(32),
            nullable=False,
            server_default="blended",
        ),
        sa.Column("environment", sa.String(64), nullable=True),
        sa.Column("calculation_version", sa.String(32), nullable=True),
        sa.Column("alpha_weight", sa.Float(), nullable=False),
        sa.Column("days_live", sa.Integer(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("research_score", sa.Float(), nullable=False),
        sa.Column("live_score", sa.Float(), nullable=False),
        sa.Column("blended_score", sa.Float(), nullable=False),
        sa.Column("research_metrics_snapshot_id", sa.String(64), nullable=True),
        sa.Column("live_metrics_snapshot_id", sa.String(64), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("blended_snapshot_id", name="pk_blended_metrics_snapshots"),
    )
    op.create_index(
        "ix_bms_strategy_id_computed_at",
        "blended_metrics_snapshots",
        ["strategy_id", "computed_at"],
    )
    op.create_index(
        "ix_bms_rebalance_run_id",
        "blended_metrics_snapshots",
        ["rebalance_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bms_rebalance_run_id", table_name="blended_metrics_snapshots")
    op.drop_index("ix_bms_strategy_id_computed_at", table_name="blended_metrics_snapshots")
    op.drop_table("blended_metrics_snapshots")

    with op.batch_alter_table("strategy_live_performance_snapshots") as batch_op:
        batch_op.drop_column("calculation_version")
        batch_op.drop_column("environment")
        batch_op.drop_column("metric_lineage_type")

    with op.batch_alter_table("metrics_summary") as batch_op:
        batch_op.drop_column("source_strategy_ids")
        batch_op.drop_column("lookback_window_days")
        batch_op.drop_column("calculation_version")
        batch_op.drop_column("environment")
        batch_op.drop_column("metric_lineage_type")

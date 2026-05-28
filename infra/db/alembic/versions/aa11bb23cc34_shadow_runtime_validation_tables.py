"""add shadow runtime validation tables (Phase 5.5)

Revision ID: aa11bb23cc34
Revises: zz09aa21bb32
Create Date: 2026-05-27 00:00:00.000000

Introduces three tables for the Shadow Runtime Validation Framework:

  shadow_runs            — root aggregate for each shadow validation run
  shadow_divergences     — individual divergence events detected per run
  shadow_comparison_snapshots — point-in-time sim vs live comparison payloads

All three tables are additive; existing runtime and simulation behavior is
unaffected when shadow mode is not active.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "aa11bb23cc34"
down_revision = "zz09aa21bb32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_runs",
        sa.Column("shadow_run_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("simulation_run_id", sa.String(64), nullable=False),
        sa.Column("live_run_id", sa.String(64), nullable=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("shadow_mode_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("total_divergences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("threshold_exceedances", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "divergence_by_category",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("promotion_eligible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("required_passing_cycles", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("config_hash", sa.String(64), nullable=True),
        sa.Column("covariance_snapshot_ref", sa.String(64), nullable=True),
        sa.Column("factor_snapshot_ref", sa.String(64), nullable=True),
        sa.Column("allocation_config_hash", sa.String(64), nullable=True),
        sa.Column(
            "optimizer_run_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.PrimaryKeyConstraint("shadow_run_id", name="pk_shadow_runs"),
    )
    op.create_index("ix_shadow_runs_strategy_id", "shadow_runs", ["strategy_id"])
    op.create_index("ix_shadow_runs_shadow_mode_type", "shadow_runs", ["shadow_mode_type"])
    op.create_index("ix_shadow_runs_status", "shadow_runs", ["status"])
    op.create_index("ix_shadow_runs_validation_status", "shadow_runs", ["validation_status"])
    op.create_index("ix_shadow_runs_started_at", "shadow_runs", ["started_at"])
    op.create_index("ix_shadow_runs_simulation_run_id", "shadow_runs", ["simulation_run_id"])
    op.create_index("ix_shadow_runs_live_run_id", "shadow_runs", ["live_run_id"])

    op.create_table(
        "shadow_divergences",
        sa.Column("divergence_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("shadow_run_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("divergence_type", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=True),
        sa.Column("strategy_id", sa.String(128), nullable=True),
        sa.Column("sim_value", sa.Float(), nullable=True),
        sa.Column("live_value", sa.Float(), nullable=True),
        sa.Column("divergence_magnitude", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("exceeds_threshold", sa.Boolean(), nullable=False),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.PrimaryKeyConstraint("divergence_id", name="pk_shadow_divergences"),
    )
    op.create_index("ix_shadow_div_shadow_run_id", "shadow_divergences", ["shadow_run_id"])
    op.create_index("ix_shadow_div_divergence_type", "shadow_divergences", ["divergence_type"])
    op.create_index("ix_shadow_div_category", "shadow_divergences", ["category"])
    op.create_index("ix_shadow_div_exceeds_threshold", "shadow_divergences", ["exceeds_threshold"])
    op.create_index("ix_shadow_div_timestamp", "shadow_divergences", ["timestamp"])
    op.create_index("ix_shadow_div_symbol", "shadow_divergences", ["symbol"])

    op.create_table(
        "shadow_comparison_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True, nullable=False),
        sa.Column("shadow_run_id", sa.String(64), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comparison_category", sa.String(32), nullable=False),
        sa.Column(
            "sim_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "live_values",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "drift_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_shadow_comparison_snapshots"),
    )
    op.create_index(
        "ix_shadow_snap_shadow_run_id", "shadow_comparison_snapshots", ["shadow_run_id"]
    )
    op.create_index(
        "ix_shadow_snap_bar_timestamp", "shadow_comparison_snapshots", ["bar_timestamp"]
    )
    op.create_index(
        "ix_shadow_snap_category", "shadow_comparison_snapshots", ["comparison_category"]
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_snap_category", table_name="shadow_comparison_snapshots")
    op.drop_index("ix_shadow_snap_bar_timestamp", table_name="shadow_comparison_snapshots")
    op.drop_index("ix_shadow_snap_shadow_run_id", table_name="shadow_comparison_snapshots")
    op.drop_table("shadow_comparison_snapshots")

    op.drop_index("ix_shadow_div_symbol", table_name="shadow_divergences")
    op.drop_index("ix_shadow_div_timestamp", table_name="shadow_divergences")
    op.drop_index("ix_shadow_div_exceeds_threshold", table_name="shadow_divergences")
    op.drop_index("ix_shadow_div_category", table_name="shadow_divergences")
    op.drop_index("ix_shadow_div_divergence_type", table_name="shadow_divergences")
    op.drop_index("ix_shadow_div_shadow_run_id", table_name="shadow_divergences")
    op.drop_table("shadow_divergences")

    op.drop_index("ix_shadow_runs_live_run_id", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_simulation_run_id", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_started_at", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_validation_status", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_status", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_shadow_mode_type", table_name="shadow_runs")
    op.drop_index("ix_shadow_runs_strategy_id", table_name="shadow_runs")
    op.drop_table("shadow_runs")

"""create simulation runs table

Revision ID: 59b23d6f9b58
Revises: 3756456ba962
Create Date: 2026-04-28 14:11:45.864447

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "59b23d6f9b58"
down_revision: str | Sequence[str] | None = "3756456ba962"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -----------------------------
    # experiments
    # -----------------------------
    op.create_table(
        "experiments",
        sa.Column("experiment_id", sa.String(length=64), primary_key=True),
        sa.Column("experiment_name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.create_index("ix_experiments_status", "experiments", ["status"])

    # -----------------------------
    # strategy_configs
    # -----------------------------
    op.create_table(
        "strategy_configs",
        sa.Column("strategy_id", sa.String(length=64), primary_key=True),
        sa.Column("config_hash", sa.String(length=128), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_type", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("config_hash", name="uq_strategy_configs_config_hash"),
    )

    # -----------------------------
    # simulation_runs
    # -----------------------------
    op.create_table(
        "simulation_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(length=64),
            sa.ForeignKey("experiments.experiment_id"),
            nullable=True,
        ),
        sa.Column(
            "strategy_id",
            sa.String(length=64),
            sa.ForeignKey("strategy_configs.strategy_id"),
            nullable=False,
        ),
        sa.Column(
            "dataset_version",
            sa.String(length=64),
            sa.ForeignKey("dataset_versions.dataset_version_id"),
            nullable=False,
        ),
        sa.Column("universe_version", sa.String(length=64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metrics_snapshot_id", sa.String(length=64), nullable=True),
    )

    op.create_index("ix_simulation_runs_experiment_id", "simulation_runs", ["experiment_id"])
    op.create_index("ix_simulation_runs_strategy_id", "simulation_runs", ["strategy_id"])
    op.create_index("ix_simulation_runs_dataset_version", "simulation_runs", ["dataset_version"])
    op.create_index("ix_simulation_runs_status", "simulation_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_simulation_runs_status", table_name="simulation_runs")
    op.drop_index("ix_simulation_runs_dataset_version", table_name="simulation_runs")
    op.drop_index("ix_simulation_runs_strategy_id", table_name="simulation_runs")
    op.drop_index("ix_simulation_runs_experiment_id", table_name="simulation_runs")
    op.drop_table("simulation_runs")

    op.drop_table("strategy_configs")

    op.drop_index("ix_experiments_status", table_name="experiments")
    op.drop_table("experiments")

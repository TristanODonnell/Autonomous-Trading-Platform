"""add runtime_job_run table

Revision ID: 857ff324e54a
Revises: 3f5e6f4b281f
Create Date: 2026-05-05 17:00:27.736899

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "857ff324e54a"
down_revision: str | Sequence[str] | None = "3f5e6f4b281f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_job_runs",
        sa.Column("job_run_id", sa.String(length=64), nullable=False),
        sa.Column("job_name", sa.String(length=128), nullable=False),
        sa.Column("parent_job_run_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(length=2048), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column(
            "input_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "output_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_run_id"],
            ["runtime_job_runs.job_run_id"],
            name=op.f("fk_runtime_job_runs_parent_job_run_id_runtime_job_runs"),
        ),
        sa.PrimaryKeyConstraint("job_run_id", name=op.f("pk_runtime_job_runs")),
    )

    op.create_index(
        op.f("ix_runtime_job_runs_job_name"),
        "runtime_job_runs",
        ["job_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_runs_status"),
        "runtime_job_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_runs_trigger_type"),
        "runtime_job_runs",
        ["trigger_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_runs_started_at"),
        "runtime_job_runs",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_runs_correlation_id"),
        "runtime_job_runs",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_runs_parent_job_run_id"),
        "runtime_job_runs",
        ["parent_job_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runtime_job_runs_parent_job_run_id"), table_name="runtime_job_runs")
    op.drop_index(op.f("ix_runtime_job_runs_correlation_id"), table_name="runtime_job_runs")
    op.drop_index(op.f("ix_runtime_job_runs_started_at"), table_name="runtime_job_runs")
    op.drop_index(op.f("ix_runtime_job_runs_trigger_type"), table_name="runtime_job_runs")
    op.drop_index(op.f("ix_runtime_job_runs_status"), table_name="runtime_job_runs")
    op.drop_index(op.f("ix_runtime_job_runs_job_name"), table_name="runtime_job_runs")

    op.drop_table("runtime_job_runs")

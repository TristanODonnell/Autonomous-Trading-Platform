"""add runtime_job_run_steps table

Revision ID: gg34hh56ii78
Revises: ff23aa45bb67
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "gg34hh56ii78"
down_revision: str | Sequence[str] | None = "ff23aa45bb67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)
    if "runtime_job_run_steps" in existing_tables:
        return

    op.create_table(
        "runtime_job_run_steps",
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_run_id", sa.String(length=64), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_run_id"],
            ["runtime_job_runs.job_run_id"],
            name=op.f("fk_runtime_job_run_steps_job_run_id_runtime_job_runs"),
        ),
        sa.PrimaryKeyConstraint("step_id", name=op.f("pk_runtime_job_run_steps")),
    )

    op.create_index(
        op.f("ix_runtime_job_run_steps_job_run_id"),
        "runtime_job_run_steps",
        ["job_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_run_steps_status"),
        "runtime_job_run_steps",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_job_run_steps_started_at"),
        "runtime_job_run_steps",
        ["started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runtime_job_run_steps_started_at"), table_name="runtime_job_run_steps")
    op.drop_index(op.f("ix_runtime_job_run_steps_status"), table_name="runtime_job_run_steps")
    op.drop_index(op.f("ix_runtime_job_run_steps_job_run_id"), table_name="runtime_job_run_steps")
    op.drop_table("runtime_job_run_steps")

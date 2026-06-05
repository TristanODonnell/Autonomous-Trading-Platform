"""Widen duration_ms columns from INTEGER to BIGINT

Revision ID: ii99jj00kk11
Revises: hh88ii99jj00
Create Date: 2026-06-01 00:00:00.000000

INTEGER (32-bit, max ~2.1 billion ms ≈ 24 days) overflows for jobs that have
been stuck in RUNNING state across long development periods. BIGINT supports
up to ~292 million years of milliseconds.

Affected tables:
  - runtime_job_runs.duration_ms
  - runtime_job_run_steps.duration_ms
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ii99jj00kk11"
down_revision = "hh88ii99jj00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "runtime_job_runs",
        "duration_ms",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        existing_nullable=True,
    )
    op.alter_column(
        "runtime_job_run_steps",
        "duration_ms",
        type_=sa.BigInteger(),
        existing_type=sa.Integer(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "runtime_job_run_steps",
        "duration_ms",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        "runtime_job_runs",
        "duration_ms",
        type_=sa.Integer(),
        existing_type=sa.BigInteger(),
        existing_nullable=True,
    )

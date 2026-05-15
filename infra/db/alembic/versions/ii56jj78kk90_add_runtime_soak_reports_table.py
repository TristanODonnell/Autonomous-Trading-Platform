"""add runtime_soak_reports table

Revision ID: ii56jj78kk90
Revises: hh45ii67jj89
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ii56jj78kk90"
down_revision: str | Sequence[str] | None = "hh45ii67jj89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)
    if "runtime_soak_reports" in existing_tables:
        return

    op.create_table(
        "runtime_soak_reports",
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runtime_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("report_id", name=op.f("pk_runtime_soak_reports")),
    )
    op.create_index(
        op.f("ix_runtime_soak_reports_checked_at"),
        "runtime_soak_reports",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_soak_reports_status"),
        "runtime_soak_reports",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_runtime_soak_reports_environment"),
        "runtime_soak_reports",
        ["environment"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runtime_soak_reports_environment"), table_name="runtime_soak_reports")
    op.drop_index(op.f("ix_runtime_soak_reports_status"), table_name="runtime_soak_reports")
    op.drop_index(op.f("ix_runtime_soak_reports_checked_at"), table_name="runtime_soak_reports")
    op.drop_table("runtime_soak_reports")

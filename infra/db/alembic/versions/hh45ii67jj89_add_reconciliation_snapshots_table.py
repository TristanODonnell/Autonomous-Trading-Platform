"""add reconciliation_snapshots table

Revision ID: hh45ii67jj89
Revises: gg34hh56ii78
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "hh45ii67jj89"
down_revision: str | Sequence[str] | None = "gg34hh56ii78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)
    if "reconciliation_snapshots" in existing_tables:
        return

    op.create_table(
        "reconciliation_snapshots",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_type", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("expected_value", sa.Numeric(24, 9), nullable=True),
        sa.Column("actual_value", sa.Numeric(24, 9), nullable=True),
        sa.Column("delta", sa.Numeric(24, 9), nullable=True),
        sa.Column("tolerance", sa.Numeric(24, 9), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_reconciliation_snapshots")),
    )

    op.create_index(
        op.f("ix_reconciliation_snapshots_run_id"),
        "reconciliation_snapshots",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_snapshots_reconciled_at"),
        "reconciliation_snapshots",
        ["reconciled_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_snapshots_check_type"),
        "reconciliation_snapshots",
        ["check_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_reconciliation_snapshots_status"),
        "reconciliation_snapshots",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reconciliation_snapshots_status"), table_name="reconciliation_snapshots")
    op.drop_index(
        op.f("ix_reconciliation_snapshots_check_type"), table_name="reconciliation_snapshots"
    )
    op.drop_index(
        op.f("ix_reconciliation_snapshots_reconciled_at"), table_name="reconciliation_snapshots"
    )
    op.drop_index(op.f("ix_reconciliation_snapshots_run_id"), table_name="reconciliation_snapshots")
    op.drop_table("reconciliation_snapshots")

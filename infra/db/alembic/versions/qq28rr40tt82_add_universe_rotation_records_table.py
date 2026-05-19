"""add universe_rotation_records table

Revision ID: qq28rr40tt82
Revises: pp27qq39ss71
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "qq28rr40tt82"
down_revision: str | Sequence[str] | None = "pp27qq39ss71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind)
    if "universe_rotation_records" in existing_tables:
        return

    op.create_table(
        "universe_rotation_records",
        sa.Column("rotation_id", sa.String(), nullable=False),
        sa.Column("rotation_type", sa.String(length=32), nullable=False),
        sa.Column("previous_universe_version_id", sa.String(), nullable=True),
        sa.Column("candidate_universe_version_id", sa.String(), nullable=True),
        sa.Column("new_universe_version_id", sa.String(), nullable=False),
        sa.Column("rebalance_run_id", sa.String(), nullable=True),
        sa.Column(
            "added_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "removed_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "retained_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("churn_pct", sa.Float(), nullable=True),
        sa.Column("force_rotation", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rotation_reason", sa.String(), nullable=True),
        sa.Column("rollback_reason", sa.String(), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rejection_reason", sa.String(), nullable=True),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("rotation_id"),
    )

    op.create_index(
        "ix_rotation_records_new_version_id",
        "universe_rotation_records",
        ["new_universe_version_id"],
    )
    op.create_index(
        "ix_rotation_records_previous_version_id",
        "universe_rotation_records",
        ["previous_universe_version_id"],
    )
    op.create_index(
        "ix_rotation_records_rotation_type",
        "universe_rotation_records",
        ["rotation_type"],
    )
    op.create_index(
        "ix_rotation_records_created_at",
        "universe_rotation_records",
        ["created_at"],
    )
    op.create_index(
        "ix_rotation_records_status",
        "universe_rotation_records",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_rotation_records_status", table_name="universe_rotation_records")
    op.drop_index("ix_rotation_records_created_at", table_name="universe_rotation_records")
    op.drop_index("ix_rotation_records_rotation_type", table_name="universe_rotation_records")
    op.drop_index("ix_rotation_records_previous_version_id", table_name="universe_rotation_records")
    op.drop_index("ix_rotation_records_new_version_id", table_name="universe_rotation_records")
    op.drop_table("universe_rotation_records")

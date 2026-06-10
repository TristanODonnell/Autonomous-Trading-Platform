"""add ingestion checkpoints table

Revision ID: c270b4c5b0b8
Revises: 60d949166807
Create Date: 2026-04-24 10:00:34.724876

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c270b4c5b0b8"
down_revision: str | Sequence[str] | None = "60d949166807"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the earlier version of this table (created in 36406d0a0f0b with
    # lowercase enum values and no indexes) so we can replace it with the
    # canonical schema.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "ingestion_checkpoints" in insp.get_table_names():
        op.drop_table("ingestion_checkpoints")
    sa.Enum(name="checkpoint_status_enum").drop(bind, checkfirst=True)
    sa.Enum(name="checkpoint_scope_enum").drop(bind, checkfirst=True)

    checkpoint_scope_enum = postgresql.ENUM(
        "BACKFILL",
        "CYCLE",
        name="checkpoint_scope_enum",
        create_type=False,
    )

    checkpoint_status_enum = postgresql.ENUM(
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "FAILED",
        name="checkpoint_status_enum",
        create_type=False,
    )

    checkpoint_scope_enum.create(bind, checkfirst=True)
    checkpoint_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "ingestion_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "ingestion_run_id",
            sa.String(length=64),
            sa.ForeignKey("ingestion_runs.ingestion_run_id"),
            nullable=False,
        ),
        sa.Column(
            "dataset_version",
            sa.String(length=64),
            sa.ForeignKey("dataset_versions.dataset_version_id"),
            nullable=False,
        ),
        sa.Column("checkpoint_scope", checkpoint_scope_enum, nullable=False),
        sa.Column("checkpoint_status", checkpoint_status_enum, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("checkpoint_date", sa.Date(), nullable=True),
        sa.Column("cycle_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_bar_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index(
        "ix_ingestion_checkpoints_run_id",
        "ingestion_checkpoints",
        ["ingestion_run_id"],
    )

    op.create_index(
        "ix_ingestion_checkpoints_dataset_version",
        "ingestion_checkpoints",
        ["dataset_version"],
    )

    op.create_index(
        "ix_ingestion_checkpoints_symbol_date",
        "ingestion_checkpoints",
        ["symbol", "checkpoint_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_checkpoints_symbol_date", table_name="ingestion_checkpoints")
    op.drop_index("ix_ingestion_checkpoints_dataset_version", table_name="ingestion_checkpoints")
    op.drop_index("ix_ingestion_checkpoints_run_id", table_name="ingestion_checkpoints")

    op.drop_table("ingestion_checkpoints")

    sa.Enum(name="checkpoint_status_enum").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="checkpoint_scope_enum").drop(op.get_bind(), checkfirst=True)

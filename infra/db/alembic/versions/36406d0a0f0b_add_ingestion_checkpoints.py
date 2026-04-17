from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "36406d0a0f0b"
down_revision: str | Sequence[str] | None = "77afde93f3e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("ingestion_run_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column(
            "checkpoint_scope",
            sa.Enum("backfill", "cycle", name="checkpoint_scope_enum"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_status",
            sa.Enum(
                "pending",
                "in_progress",
                "completed",
                "failed",
                name="checkpoint_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("checkpoint_date", sa.Date(), nullable=True),
        sa.Column("cycle_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_successful_bar_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version"],
            ["dataset_versions.dataset_version_id"],
            name=op.f("fk_ingestion_checkpoints_dataset_version_dataset_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.ingestion_run_id"],
            name=op.f("fk_ingestion_checkpoints_ingestion_run_id_ingestion_runs"),
        ),
        sa.PrimaryKeyConstraint("checkpoint_id", name=op.f("pk_ingestion_checkpoints")),
    )


def downgrade() -> None:
    op.drop_table("ingestion_checkpoints")

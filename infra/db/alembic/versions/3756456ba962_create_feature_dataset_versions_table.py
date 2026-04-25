"""create feature dataset versions table

Revision ID: 3756456ba962
Revises: c270b4c5b0b8
Create Date: 2026-04-24 10:59:10.323765

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3756456ba962"
down_revision: str | Sequence[str] | None = "c270b4c5b0b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "feature_dataset_versions",
        sa.Column("dataset_version_id", sa.String(length=64), primary_key=True),
        sa.Column("feature_name", sa.String(length=128), nullable=False),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("source_dataset_version", sa.String(length=64), nullable=False),
        sa.Column(
            "underlying_price_basis",
            postgresql.ENUM(
                "RAW",
                "ADJUSTED",
                name="price_basis_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("computation_parameters", postgresql.JSONB(), nullable=True),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("symbol_coverage", sa.Integer(), nullable=True),
        sa.Column("date_coverage_start", sa.Date(), nullable=True),
        sa.Column("date_coverage_end", sa.Date(), nullable=True),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("source_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("computation_code_version", sa.String(length=512), nullable=False),
    )

    op.create_index(
        "ix_feature_dataset_versions_feature_source",
        "feature_dataset_versions",
        ["feature_name", "source_dataset_version"],
    )

    op.create_index(
        "ix_feature_dataset_versions_validation_status",
        "feature_dataset_versions",
        ["validation_status"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_feature_dataset_versions_validation_status",
        table_name="feature_dataset_versions",
    )
    op.drop_index(
        "ix_feature_dataset_versions_feature_source",
        table_name="feature_dataset_versions",
    )
    op.drop_table("feature_dataset_versions")

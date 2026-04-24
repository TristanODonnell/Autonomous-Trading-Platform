"""sync dataset_versions columns

Revision ID: 62066786c890
Revises: 77aeb5b5252f
Create Date: 2026-04-23 15:35:03.603365

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "62066786c890"
down_revision: str | Sequence[str] | None = "77aeb5b5252f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions", sa.Column("schema_version", sa.String(length=32), nullable=True)
    )
    op.add_column("dataset_versions", sa.Column("symbol_coverage", sa.Integer(), nullable=True))
    op.add_column("dataset_versions", sa.Column("date_coverage_start", sa.Date(), nullable=True))
    op.add_column("dataset_versions", sa.Column("date_coverage_end", sa.Date(), nullable=True))
    op.add_column("dataset_versions", sa.Column("checksum", sa.String(length=128), nullable=True))
    op.add_column(
        "dataset_versions",
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.execute("UPDATE dataset_versions SET schema_version = '1.0' WHERE schema_version IS NULL")

    op.alter_column("dataset_versions", "schema_version", nullable=False)


def downgrade() -> None:
    op.drop_column("dataset_versions", "source_manifest")
    op.drop_column("dataset_versions", "checksum")
    op.drop_column("dataset_versions", "date_coverage_end")
    op.drop_column("dataset_versions", "date_coverage_start")
    op.drop_column("dataset_versions", "symbol_coverage")
    op.drop_column("dataset_versions", "schema_version")

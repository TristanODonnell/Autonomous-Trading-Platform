"""add artifact_manifest and schema_definition to run_manifests

Revision ID: 4981b5a58d62
Revises: 1ee1ec15f1a8
Create Date: 2026-04-14 16:12:30.711906
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4981b5a58d62"
down_revision: str | Sequence[str] | None = "1ee1ec15f1a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "run_manifests",
        sa.Column("artifact_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column("schema_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("run_manifests", "schema_definition")
    op.drop_column("run_manifests", "artifact_manifest")

"""add INGESTION to run_type_enum

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-05-12 00:00:02.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a1b2"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.execute("ALTER TYPE run_type_enum ADD VALUE IF NOT EXISTS 'INGESTION'")


def downgrade():
    pass

"""add CORPORATE_ACTION_INGESTION to run_type_enum

Revision ID: a1b2c3d4e5f6
Revises: 8809a513a5ef
Create Date: 2026-05-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "8809a513a5ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.execute("ALTER TYPE run_type_enum ADD VALUE IF NOT EXISTS 'CORPORATE_ACTION_INGESTION'")


def downgrade():
    pass

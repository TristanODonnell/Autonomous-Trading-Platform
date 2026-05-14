"""add governance run type enum

Revision ID: ab12cd34ef56
Revises: e7f8a9b0c1d2
Create Date: 2026-05-13 00:00:00.000000
"""

from alembic import op

revision = "ab12cd34ef56"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE run_type_enum ADD VALUE IF NOT EXISTS 'governance'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely without recreating the enum.
    pass

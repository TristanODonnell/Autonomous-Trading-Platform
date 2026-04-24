"""add BACKFILL to run_type_enum

Revision ID: 77aeb5b5252f
Revises: 36406d0a0f0b
Create Date: 2026-04-23 14:55:55.655632

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77aeb5b5252f"
down_revision: str | Sequence[str] | None = "36406d0a0f0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE run_type_enum ADD VALUE IF NOT EXISTS 'BACKFILL'")


def downgrade() -> None:
    pass

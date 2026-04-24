"""sync checkpoint enum values

Revision ID: 60d949166807
Revises: 62066786c890
Create Date: 2026-04-23 16:24:56.405011

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "60d949166807"
down_revision: str | Sequence[str] | None = "62066786c890"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE checkpoint_scope_enum ADD VALUE IF NOT EXISTS 'BACKFILL'")
    op.execute("ALTER TYPE checkpoint_scope_enum ADD VALUE IF NOT EXISTS 'CYCLE'")

    op.execute("ALTER TYPE checkpoint_status_enum ADD VALUE IF NOT EXISTS 'PENDING'")
    op.execute("ALTER TYPE checkpoint_status_enum ADD VALUE IF NOT EXISTS 'IN_PROGRESS'")
    op.execute("ALTER TYPE checkpoint_status_enum ADD VALUE IF NOT EXISTS 'COMPLETED'")
    op.execute("ALTER TYPE checkpoint_status_enum ADD VALUE IF NOT EXISTS 'FAILED'")


def downgrade() -> None:
    """Downgrade schema."""
    pass

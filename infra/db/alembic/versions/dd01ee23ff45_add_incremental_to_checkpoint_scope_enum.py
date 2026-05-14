"""add INCREMENTAL to checkpoint_scope_enum

Revision ID: dd01ee23ff45
Revises: bc23de45fg67
Create Date: 2026-05-13 19:27:41.364644

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd01ee23ff45"
down_revision: str | Sequence[str] | None = "bc23de45fg67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE checkpoint_scope_enum ADD VALUE IF NOT EXISTS 'INCREMENTAL'")


def downgrade() -> None:
    pass

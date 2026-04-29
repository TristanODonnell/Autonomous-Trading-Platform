"""add SIMULATION to run_type_enum

Revision ID: 8af2897c5784
Revises: d7e129a4cae3
Create Date: 2026-04-28 19:06:31.416760

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8af2897c5784"
down_revision: str | Sequence[str] | None = "d7e129a4cae3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.execute("ALTER TYPE run_type_enum ADD VALUE 'SIMULATION'")


def downgrade():
    pass

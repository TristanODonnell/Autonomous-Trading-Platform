"""Add SIMULATION and BACKTEST to order_source_enum

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE order_source_enum ADD VALUE IF NOT EXISTS 'SIMULATION'")
    op.execute("ALTER TYPE order_source_enum ADD VALUE IF NOT EXISTS 'BACKTEST'")


def downgrade() -> None:
    # Postgres does not support removing enum values; downgrade is a no-op.
    pass

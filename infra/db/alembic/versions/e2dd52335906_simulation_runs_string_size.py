"""simulation_runs string size

Revision ID: e2dd52335906
Revises: 4817529eb17c
Create Date: 2026-04-29 19:29:35.220520

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2dd52335906"
down_revision: str | Sequence[str] | None = "4817529eb17c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "simulation_runs",
        "strategy_id",
        existing_type=sa.VARCHAR(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "simulation_runs",
        "strategy_id",
        existing_type=sa.String(length=128),
        type_=sa.VARCHAR(length=64),
        existing_nullable=False,
    )

"""strategy config string revision

Revision ID: 4817529eb17c
Revises: 72bcc9eb202b
Create Date: 2026-04-29 19:17:47.678898

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4817529eb17c"
down_revision: str | Sequence[str] | None = "72bcc9eb202b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "strategy_configs",
        "strategy_id",
        existing_type=sa.VARCHAR(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "strategy_configs",
        "strategy_id",
        existing_type=sa.String(length=128),
        type_=sa.VARCHAR(length=64),
        existing_nullable=False,
    )

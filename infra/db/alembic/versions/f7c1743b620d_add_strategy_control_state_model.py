"""Add strategy_control_state model

Revision ID: f7c1743b620d
Revises: 951c00580d2b
Create Date: 2026-05-08 11:44:37.708698

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from autonomous_trading_platform.storage.sor.models.helpers.sa_types import (
    UTCDateTimeType,
)

# revision identifiers, used by Alembic.
revision: str = "f7c1743b620d"
down_revision: str | Sequence[str] | None = "951c00580d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_control_states",
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("updated_at", UTCDateTimeType(), nullable=False),
        sa.PrimaryKeyConstraint(
            "strategy_id",
            name=op.f("pk_strategy_control_states"),
        ),
    )


def downgrade() -> None:
    op.drop_table("strategy_control_states")

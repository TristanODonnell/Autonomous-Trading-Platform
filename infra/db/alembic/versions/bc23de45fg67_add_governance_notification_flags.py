"""Add governance notification flags to operator_settings

Revision ID: bc23de45fg67
Revises: ab12cd34ef56
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bc23de45fg67"
down_revision: str | Sequence[str] | None = "ab12cd34ef56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "notify_strategy_demotion_events",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "notify_allocation_rebalance_events",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.alter_column(
        "operator_settings",
        "notify_strategy_demotion_events",
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "operator_settings",
        "notify_allocation_rebalance_events",
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "notify_allocation_rebalance_events")
    op.drop_column("operator_settings", "notify_strategy_demotion_events")

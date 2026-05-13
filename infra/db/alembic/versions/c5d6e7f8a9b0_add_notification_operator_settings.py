"""Add notification fields to operator_settings

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "notify_drawdown_alerts",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "notify_strategy_promotion_events",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "notify_pipeline_failures",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.alter_column(
        "operator_settings",
        "notify_drawdown_alerts",
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "operator_settings",
        "notify_strategy_promotion_events",
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "operator_settings",
        "notify_pipeline_failures",
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "notify_pipeline_failures")
    op.drop_column("operator_settings", "notify_strategy_promotion_events")
    op.drop_column("operator_settings", "notify_drawdown_alerts")

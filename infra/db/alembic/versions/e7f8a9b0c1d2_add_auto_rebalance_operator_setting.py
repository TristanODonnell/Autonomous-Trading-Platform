"""Add auto_rebalance_enabled to operator_settings

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "auto_rebalance_enabled",
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "operator_settings",
        "auto_rebalance_enabled",
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "auto_rebalance_enabled")

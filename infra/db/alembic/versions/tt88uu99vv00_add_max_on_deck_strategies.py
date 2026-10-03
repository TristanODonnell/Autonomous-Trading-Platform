"""add max_on_deck_strategies to operator_settings

Portfolio rotation step 2B: cap on the on-deck shadow tier (default 10; 0 disables).

Revision ID: tt88uu99vv00
Revises: ss77tt88uu99
Create Date: 2026-09-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "tt88uu99vv00"
down_revision = "ss77tt88uu99"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column("max_on_deck_strategies", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "max_on_deck_strategies")

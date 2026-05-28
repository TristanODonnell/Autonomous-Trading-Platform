"""add promotion maintenance rule fields

Revision ID: xx87yy99zz10
Revises: ww76xx88yy09
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "xx87yy99zz10"
down_revision = "ww76xx88yy09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("promotion_rules", sa.Column("lookback_window_bars", sa.Integer(), nullable=True))
    op.add_column(
        "promotion_rules",
        sa.Column("lookback_window_trades", sa.Integer(), nullable=True),
    )
    op.add_column(
        "promotion_rules",
        sa.Column("maintenance_min_sharpe", sa.Float(), nullable=True),
    )
    op.add_column(
        "promotion_rules",
        sa.Column("maintenance_min_win_rate", sa.Float(), nullable=True),
    )
    op.add_column(
        "promotion_rules",
        sa.Column("maintenance_max_drawdown", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("promotion_rules", "maintenance_max_drawdown")
    op.drop_column("promotion_rules", "maintenance_min_win_rate")
    op.drop_column("promotion_rules", "maintenance_min_sharpe")
    op.drop_column("promotion_rules", "lookback_window_trades")
    op.drop_column("promotion_rules", "lookback_window_bars")

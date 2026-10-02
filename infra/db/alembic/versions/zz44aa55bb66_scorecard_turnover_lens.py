"""add the turnover lens to portfolio scorecards

Rotation step 5c-G: the review subtracts a penalty for re-sim turnover above 2x the
capital per day (noise traders turned their sleeve over 13-26x a day).

Revision ID: zz44aa55bb66
Revises: yy33zz44aa55
Create Date: 2026-10-02

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "zz44aa55bb66"
down_revision = "yy33zz44aa55"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portfolio_scorecards", sa.Column("daily_turnover", sa.Float(), nullable=True))
    op.add_column(
        "portfolio_scorecards",
        sa.Column(
            "turnover_penalty", sa.Numeric(12, 6), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_scorecards", "turnover_penalty")
    op.drop_column("portfolio_scorecards", "daily_turnover")

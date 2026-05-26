"""Add portfolio symbol exposure limit columns to operator_settings

Revision ID: ww76xx88yy09
Revises: vv65ww77xx08
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ww76xx88yy09"
down_revision: str | None = "vv65ww77xx08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "max_portfolio_symbol_exposure_usd",
            sa.Numeric(16, 2),
            nullable=True,
            server_default=None,
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "max_portfolio_symbol_pct",
            sa.Numeric(6, 4),
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "max_portfolio_symbol_pct")
    op.drop_column("operator_settings", "max_portfolio_symbol_exposure_usd")

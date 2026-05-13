"""Add strategy drawdown and target volatility to operator_settings

Revision ID: a9b8c7d6e5f4
Revises: f6a1b2c3d4e5
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "f6a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "max_strategy_drawdown",
            sa.Numeric(precision=6, scale=4),
            nullable=True,
            server_default="0.1200",
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "target_portfolio_volatility",
            sa.Numeric(precision=6, scale=4),
            nullable=True,
            server_default="0.1500",
        ),
    )
    op.create_check_constraint(
        "ck_operator_settings_max_strategy_drawdown_range",
        "operator_settings",
        "max_strategy_drawdown >= 0.0000 AND max_strategy_drawdown <= 1.0000",
    )
    op.create_check_constraint(
        "ck_operator_settings_target_portfolio_volatility_range",
        "operator_settings",
        "target_portfolio_volatility > 0.0000 AND target_portfolio_volatility <= 1.0000",
    )
    op.alter_column(
        "operator_settings", "max_strategy_drawdown", nullable=False, server_default=None
    )
    op.alter_column(
        "operator_settings",
        "target_portfolio_volatility",
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_operator_settings_target_portfolio_volatility_range",
        "operator_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_operator_settings_max_strategy_drawdown_range",
        "operator_settings",
        type_="check",
    )
    op.drop_column("operator_settings", "target_portfolio_volatility")
    op.drop_column("operator_settings", "max_strategy_drawdown")

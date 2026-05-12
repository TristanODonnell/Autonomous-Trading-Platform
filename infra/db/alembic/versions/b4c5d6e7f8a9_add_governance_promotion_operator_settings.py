"""Add governance promotion fields to operator_settings

Revision ID: b4c5d6e7f8a9
Revises: a9b8c7d6e5f4
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "min_sharpe_for_promotion",
            sa.Numeric(precision=6, scale=3),
            nullable=True,
            server_default="1.500",
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "min_paper_trading_period_days",
            sa.Integer(),
            nullable=True,
            server_default="30",
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "auto_demote_on_breach",
            sa.Boolean(),
            nullable=True,
            server_default=sa.true(),
        ),
    )
    op.create_check_constraint(
        "ck_operator_settings_min_sharpe_for_promotion_range",
        "operator_settings",
        "min_sharpe_for_promotion >= 0.000 AND min_sharpe_for_promotion <= 10.000",
    )
    op.create_check_constraint(
        "ck_operator_settings_min_paper_trading_period_days_range",
        "operator_settings",
        "min_paper_trading_period_days >= 1 AND min_paper_trading_period_days <= 365",
    )
    op.alter_column(
        "operator_settings",
        "min_sharpe_for_promotion",
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "operator_settings",
        "min_paper_trading_period_days",
        nullable=False,
        server_default=None,
    )
    op.alter_column(
        "operator_settings",
        "auto_demote_on_breach",
        nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_operator_settings_min_paper_trading_period_days_range",
        "operator_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_operator_settings_min_sharpe_for_promotion_range",
        "operator_settings",
        type_="check",
    )
    op.drop_column("operator_settings", "auto_demote_on_breach")
    op.drop_column("operator_settings", "min_paper_trading_period_days")
    op.drop_column("operator_settings", "min_sharpe_for_promotion")

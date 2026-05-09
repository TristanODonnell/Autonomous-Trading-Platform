"""add operator settings model table

Revision ID: 3467a0ff2319
Revises: f7c1743b620d
Create Date: 2026-05-09 14:42:26.152371
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3467a0ff2319"
down_revision: str | Sequence[str] | None = "f7c1743b620d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operator_settings",
        sa.Column("settings_id", sa.String(length=64), nullable=False),
        sa.Column("risk_tolerance", sa.String(length=16), nullable=False),
        sa.Column("max_drawdown_limit", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("rebalance_frequency", sa.String(length=16), nullable=False),
        sa.Column("auto_promote_enabled", sa.Boolean(), nullable=False),
        sa.Column("per_strategy_cap", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("settings_id", name=op.f("pk_operator_settings")),
    )

    op.create_check_constraint(
        "ck_operator_settings_risk_tolerance",
        "operator_settings",
        "risk_tolerance IN ('low', 'medium', 'high')",
    )

    op.create_check_constraint(
        "ck_operator_settings_rebalance_frequency",
        "operator_settings",
        "rebalance_frequency IN ('daily', 'weekly', 'monthly')",
    )

    op.create_check_constraint(
        "ck_operator_settings_max_drawdown_limit_range",
        "operator_settings",
        "max_drawdown_limit >= 0.0000 AND max_drawdown_limit <= 1.0000",
    )

    op.create_check_constraint(
        "ck_operator_settings_per_strategy_cap_range",
        "operator_settings",
        "per_strategy_cap >= 0.0000 AND per_strategy_cap <= 1.0000",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_operator_settings_per_strategy_cap_range",
        "operator_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_operator_settings_max_drawdown_limit_range",
        "operator_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_operator_settings_rebalance_frequency",
        "operator_settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_operator_settings_risk_tolerance",
        "operator_settings",
        type_="check",
    )
    op.drop_table("operator_settings")

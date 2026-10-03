"""add portfolio membership and active-set limits

Portfolio rotation step 1C:

  portfolio_memberships             current status per strategy (active / winding_down / ...)
  portfolio_membership_transitions  append-only audit of status changes
  operator_settings                 portfolio_mode_enabled (default off),
                                    min_active_strategies, max_active_strategies

Revision ID: rr66ss77tt88
Revises: qq55rr66ss77
Create Date: 2026-09-26

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "rr66ss77tt88"
down_revision = "qq55rr66ss77"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "portfolio_memberships",
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("since", _TS, nullable=False),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("strategy_id", name="pk_portfolio_memberships"),
    )
    op.create_index("ix_pm_status", "portfolio_memberships", ["status"])

    op.create_table(
        "portfolio_membership_transitions",
        sa.Column("transition_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("triggered_by", sa.String(128), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("transition_id", name="pk_portfolio_membership_transitions"),
    )
    op.create_index(
        "ix_pmt_strategy_created",
        "portfolio_membership_transitions",
        ["strategy_id", "created_at"],
    )

    op.add_column(
        "operator_settings",
        sa.Column(
            "portfolio_mode_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column("min_active_strategies", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "operator_settings",
        sa.Column("max_active_strategies", sa.Integer(), nullable=False, server_default="6"),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "max_active_strategies")
    op.drop_column("operator_settings", "min_active_strategies")
    op.drop_column("operator_settings", "portfolio_mode_enabled")
    op.drop_index("ix_pmt_strategy_created", table_name="portfolio_membership_transitions")
    op.drop_table("portfolio_membership_transitions")
    op.drop_index("ix_pm_status", table_name="portfolio_memberships")
    op.drop_table("portfolio_memberships")

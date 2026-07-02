"""Create tables missing from migration history: portfolio_drawdown_governance_state, ticker_lifecycle_events

Revision ID: oo33pp44qq55
Revises: nn22oo33pp44
Create Date: 2026-06-07

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "oo33pp44qq55"
down_revision = "nn22oo33pp44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "ticker_lifecycle_events" not in existing:
        op.create_table(
            "ticker_lifecycle_events",
            sa.Column("event_id", sa.String(), primary_key=True),
            sa.Column("symbol", sa.String(), nullable=False, index=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("successor_symbol", sa.String(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("event_id", name="pk_ticker_lifecycle_events"),
        )

    if "portfolio_drawdown_governance_state" not in existing:
        op.create_table(
            "portfolio_drawdown_governance_state",
            sa.Column("governance_id", sa.String(), primary_key=True),
            sa.Column("peak_equity_usd", sa.Float(), nullable=True),
            sa.Column("governance_paused", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("rebalancing_paused", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("breach_detected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("breach_drawdown_pct", sa.Float(), nullable=True),
            sa.Column("breach_threshold_pct", sa.Float(), nullable=True),
            sa.Column("triggered_action", sa.String(64), nullable=True),
            sa.Column(
                "kill_switch_auto_activated", sa.Boolean(), nullable=False, server_default="false"
            ),
            sa.Column("operator_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("operator_acknowledged_by", sa.String(128), nullable=True),
            sa.Column("recovery_mode", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("governance_id", name="pk_portfolio_drawdown_governance_state"),
        )


def downgrade() -> None:
    op.drop_table("portfolio_drawdown_governance_state")
    op.drop_table("ticker_lifecycle_events")

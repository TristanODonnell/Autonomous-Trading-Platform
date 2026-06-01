"""Schema drift fixes: order_status_enum values + operator_settings drawdown columns

Revision ID: hh88ii99jj00
Revises: dd33ee45ff66, aa11bb23cc34, bbb10cc22dd55, aa57bb69cc80, gg77hh88ii99
Create Date: 2026-06-01 00:00:00.000000

Merges all current branch heads and applies two drift fixes:

1. order_status_enum — adds PENDING_NEW, PENDING_CANCEL, EXPIRED which exist in
   OrderStatus (contracts/common/enums.py) but were never added to the DB type.

2. operator_settings — adds portfolio_max_drawdown_pct, portfolio_drawdown_action,
   portfolio_drawdown_recovery_mode columns that exist in the ORM model
   (storage/sor/models/operator_settings.py, added for FINDING-16) but had no
   corresponding migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "hh88ii99jj00"
down_revision = (
    "dd33ee45ff66",
    "aa11bb23cc34",
    "bbb10cc22dd55",
    "aa57bb69cc80",
    "gg77hh88ii99",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. order_status_enum — add missing values
    #    PostgreSQL requires each ADD VALUE in its own statement and does
    #    not support transactional ADD VALUE before PG 14; using
    #    IF NOT EXISTS guards for idempotency.
    # ------------------------------------------------------------------
    op.execute("ALTER TYPE order_status_enum ADD VALUE IF NOT EXISTS 'PENDING_NEW'")
    op.execute("ALTER TYPE order_status_enum ADD VALUE IF NOT EXISTS 'PENDING_CANCEL'")
    op.execute("ALTER TYPE order_status_enum ADD VALUE IF NOT EXISTS 'EXPIRED'")

    # ------------------------------------------------------------------
    # 2. operator_settings — add portfolio drawdown governance columns
    # ------------------------------------------------------------------
    op.add_column(
        "operator_settings",
        sa.Column(
            "portfolio_max_drawdown_pct",
            sa.Numeric(6, 4),
            nullable=True,
            server_default=sa.text("0.15"),
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "portfolio_drawdown_action",
            sa.String(32),
            nullable=True,
            server_default=sa.text("'pause_new_trading'"),
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "portfolio_drawdown_recovery_mode",
            sa.String(32),
            nullable=True,
            server_default=sa.text("'manual_resume_required'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "portfolio_drawdown_recovery_mode")
    op.drop_column("operator_settings", "portfolio_drawdown_action")
    op.drop_column("operator_settings", "portfolio_max_drawdown_pct")
    # NOTE: PostgreSQL does not support DROP VALUE from an enum type.
    # To fully downgrade order_status_enum, recreate the type without the added values.

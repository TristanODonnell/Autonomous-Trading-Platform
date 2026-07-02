"""add PENDING_NEW, PENDING_CANCEL, EXPIRED to tracked_order_status_enum

Revision ID: nn22oo33pp44
Revises: kk11ll22mm33
Create Date: 2026-06-07

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "nn22oo33pp44"
down_revision = "kk11ll22mm33"
branch_labels = None
depends_on = None

_ALL_ORDER_STATUS_VALUES = (
    "NEW",
    "PENDING_NEW",
    "SUBMITTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "PENDING_CANCEL",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # On a fresh database tracked_orders was never created by a prior migration.
    # Create the enum type and table here if they're missing.
    existing_enums = {
        row[0] for row in bind.execute(sa.text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
    }
    if "tracked_order_status_enum" not in existing_enums:
        enum_type = postgresql.ENUM(
            *_ALL_ORDER_STATUS_VALUES,
            name="tracked_order_status_enum",
            create_type=False,
        )
        enum_type.create(bind, checkfirst=True)

    if "tracked_orders" not in insp.get_table_names():
        op.create_table(
            "tracked_orders",
            sa.Column("order_id", sa.UUID(), primary_key=True),
            sa.Column("intent_id", sa.UUID(), nullable=False),
            sa.Column("run_id", sa.UUID(), nullable=False),
            sa.Column("strategy_id", sa.String(128), nullable=False),
            sa.Column("account_id", sa.String(64), nullable=False),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("broker_order_id", sa.String(64), nullable=True),
            sa.Column(
                "current_status",
                postgresql.ENUM(
                    *_ALL_ORDER_STATUS_VALUES, name="tracked_order_status_enum", create_type=False
                ),
                nullable=False,
            ),
            sa.Column("previous_filled_qty", sa.Numeric(18, 9), nullable=False, server_default="0"),
            sa.Column("previous_avg_fill_price", sa.Numeric(18, 6), nullable=True),
            sa.Column("is_open", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("order_id", name="pk_tracked_orders"),
        )

    # Add any missing enum values (safe on existing DBs too).
    for value in ("PENDING_NEW", "PENDING_CANCEL", "EXPIRED"):
        op.execute(f"ALTER TYPE tracked_order_status_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; downgrade is a no-op.
    pass

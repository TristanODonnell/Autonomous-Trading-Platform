"""add tracked_orders table

Revision ID: <new_revision_id>
Revises: 8943572f1891
Create Date: <timestamp>
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from autonomous_trading_platform.storage.sor.models.helpers.sa_types import (
    UUID_PK,
    MoneyType,
    QuantityType,
    UTCDateTimeType,
)

revision: str = "<new_revision_id>"
down_revision: str | Sequence[str] | None = "8943572f1891"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracked_orders",
        sa.Column("order_id", UUID_PK, nullable=False),
        sa.Column("intent_id", UUID_PK, nullable=False),
        sa.Column("run_id", UUID_PK, nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("broker_order_id", sa.String(length=64), nullable=True),
        sa.Column(
            "current_status",
            sa.Enum(
                "NEW",
                "SUBMITTED",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELED",
                "REJECTED",
                name="tracked_order_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("previous_filled_qty", QuantityType(), nullable=False),
        sa.Column("previous_avg_fill_price", MoneyType(), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTimeType(timezone=True), nullable=False),
        sa.Column("updated_at", UTCDateTimeType(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_tracked_orders")),
    )


def downgrade() -> None:
    op.drop_table("tracked_orders")
    sa.Enum(name="tracked_order_status_enum").drop(op.get_bind(), checkfirst=True)

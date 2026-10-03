"""index the trading cycle's per-tick lookups on growing tables

Rotation step 5c-H: every 5-minute tick reads the latest cash and position snapshot
(ORDER BY timestamp DESC LIMIT 1), open / reconcilable tracked orders and a strategy's
latest order intents. None of these had an index, so each call scanned and sorted a table
that grows for the whole run (a 4-month backtest slowed from ~5 to ~11 min per day).

Revision ID: yy33zz44aa55
Revises: xx22yy33zz44
Create Date: 2026-10-02

"""

from __future__ import annotations

from alembic import op

revision = "yy33zz44aa55"
down_revision = "xx22yy33zz44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_cash_snapshots_timestamp", "cash_snapshots", ["timestamp"])
    op.create_index("ix_position_snapshots_timestamp", "position_snapshots", ["timestamp"])
    op.create_index("ix_tracked_orders_is_open", "tracked_orders", ["is_open"])
    op.create_index("ix_tracked_orders_current_status", "tracked_orders", ["current_status"])
    op.create_index(
        "ix_order_intents_strategy_timestamp", "order_intents", ["strategy_id", "timestamp"]
    )


def downgrade() -> None:
    op.drop_index("ix_order_intents_strategy_timestamp", table_name="order_intents")
    op.drop_index("ix_tracked_orders_current_status", table_name="tracked_orders")
    op.drop_index("ix_tracked_orders_is_open", table_name="tracked_orders")
    op.drop_index("ix_position_snapshots_timestamp", table_name="position_snapshots")
    op.drop_index("ix_cash_snapshots_timestamp", table_name="cash_snapshots")

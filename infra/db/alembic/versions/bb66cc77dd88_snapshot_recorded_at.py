"""record when cash and position snapshots were written

Several cash snapshots share one `timestamp` within a trading cycle (one per fill) and
the "latest" read broke the tie on the random snapshot id, so the daily portfolio
snapshot (and the risk and drawdown-governance equity built on it) could read a stale
cash balance against post-fill positions. `recorded_at` records write order; rows from
before this column fall back to their `timestamp`.

Revision ID: bb66cc77dd88
Revises: aa55bb66cc77
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "bb66cc77dd88"
down_revision = "aa55bb66cc77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("cash_snapshots", "position_snapshots"):
        op.add_column(table, sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(f"UPDATE {table} SET recorded_at = timestamp WHERE recorded_at IS NULL")


def downgrade() -> None:
    for table in ("position_snapshots", "cash_snapshots"):
        op.drop_column(table, "recorded_at")

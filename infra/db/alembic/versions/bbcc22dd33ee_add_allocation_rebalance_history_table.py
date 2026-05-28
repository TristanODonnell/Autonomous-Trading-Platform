"""Add allocation_rebalance_history table

Persists execution metadata for every rebalance attempt:
  - status: running | completed | noop | skipped_interval | skipped_concurrent
            | skipped_disabled | failed
  - skipped_reason: textual reason when not applied
  - turnover fields: total_allocation_delta_pct, churn_pct
  - allocation_changes_count: number of strategy overrides written

Used by:
  - Interval guard: reads last completed_at to enforce min_rebalance_interval_hours
  - Concurrent lock: reads "running" rows to prevent overlapping executions
  - Observability: audit trail for all rebalance decisions

Revision ID: bbcc22dd33ee
Revises: aabb11cc22dd
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bbcc22dd33ee"
down_revision: str | Sequence[str] | None = "aabb11cc22dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind)
    if "allocation_rebalance_history" in existing_tables:
        return

    op.create_table(
        "allocation_rebalance_history",
        sa.Column("rebalance_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("trigger_source", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("skipped_reason", sa.String(256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategies_evaluated", sa.Integer(), nullable=True),
        sa.Column("allocation_changes_count", sa.Integer(), nullable=True),
        sa.Column("total_allocation_delta_pct", sa.Float(), nullable=True),
        sa.Column("churn_pct", sa.Float(), nullable=True),
        sa.Column(
            "result_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("rebalance_id"),
    )
    op.create_index("ix_arh_started_at", "allocation_rebalance_history", ["started_at"])
    op.create_index("ix_arh_status", "allocation_rebalance_history", ["status"])
    op.create_index("ix_arh_run_id", "allocation_rebalance_history", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_arh_run_id", table_name="allocation_rebalance_history")
    op.drop_index("ix_arh_status", table_name="allocation_rebalance_history")
    op.drop_index("ix_arh_started_at", table_name="allocation_rebalance_history")
    op.drop_table("allocation_rebalance_history")

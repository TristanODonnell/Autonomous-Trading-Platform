"""Add rebalance stability columns to operator_settings

Adds:
  - min_rebalance_interval_hours: minimum hours between rebalance executions (default 24)
  - min_allocation_change_pct: hysteresis threshold; overrides skipped if delta < this (default 1%)
  - turnover_penalty_weight: optional scoring penalty weight for allocation turnover (default NULL)

Revision ID: aabb11cc22dd
Revises: zz09aa21bb32
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "aabb11cc22dd"
down_revision: str | Sequence[str] | None = "zz09aa21bb32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "min_rebalance_interval_hours",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="24.0",
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "min_allocation_change_pct",
            sa.Numeric(6, 4),
            nullable=False,
            server_default="0.01",
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "turnover_penalty_weight",
            sa.Numeric(8, 4),
            nullable=True,
            server_default=None,
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "turnover_penalty_weight")
    op.drop_column("operator_settings", "min_allocation_change_pct")
    op.drop_column("operator_settings", "min_rebalance_interval_hours")

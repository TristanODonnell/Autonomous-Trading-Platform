"""create missing metrics summary table

Revision ID: 44f3da085c13
Revises: 42ade1fb045a
Create Date: 2026-04-29 15:03:19.314760

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "44f3da085c13"
down_revision: str | Sequence[str] | None = "42ade1fb045a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metrics_summary",
        sa.Column("metrics_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_return", sa.Float(), nullable=True),
        sa.Column("sharpe_ratio", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("trade_count", sa.Integer(), nullable=True),
        sa.Column("winning_trade_count", sa.Integer(), nullable=True),
        sa.Column("losing_trade_count", sa.Integer(), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("metrics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["simulation_runs.run_id"]),
        sa.PrimaryKeyConstraint("metrics_snapshot_id"),
    )


def downgrade() -> None:
    op.drop_table("metrics_summary")

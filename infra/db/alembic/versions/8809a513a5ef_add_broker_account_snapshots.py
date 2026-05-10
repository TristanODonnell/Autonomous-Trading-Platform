"""add broker account snapshots

Revision ID: 8809a513a5ef
Revises: a85c2ede6d4e
Create Date: 2026-05-09 18:56:52.154770
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8809a513a5ef"
down_revision: str | Sequence[str] | None = "a85c2ede6d4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_account_snapshots",
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("broker", sa.String(length=32), nullable=False),
        sa.Column("trading_environment", sa.String(length=16), nullable=False),
        sa.Column("broker_account_id", sa.String(length=64), nullable=False),
        sa.Column("account_status", sa.String(length=64), nullable=True),
        sa.Column("cash", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("buying_power", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("equity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("portfolio_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id", name=op.f("pk_broker_account_snapshots")),
    )

    op.create_index(
        op.f("ix_broker_account_snapshots_broker_account_id"),
        "broker_account_snapshots",
        ["broker_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_broker_account_snapshots_broker_environment"),
        "broker_account_snapshots",
        ["broker", "trading_environment"],
        unique=False,
    )
    op.create_index(
        op.f("ix_broker_account_snapshots_observed_at"),
        "broker_account_snapshots",
        ["observed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_broker_account_snapshots_observed_at"),
        table_name="broker_account_snapshots",
    )
    op.drop_index(
        op.f("ix_broker_account_snapshots_broker_environment"),
        table_name="broker_account_snapshots",
    )
    op.drop_index(
        op.f("ix_broker_account_snapshots_broker_account_id"),
        table_name="broker_account_snapshots",
    )
    op.drop_table("broker_account_snapshots")

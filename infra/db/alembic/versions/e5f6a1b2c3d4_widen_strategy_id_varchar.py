"""Widen strategy_id VARCHAR(64) to VARCHAR(128) across governance-related tables

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-05-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a1b2c3d4"
down_revision: str | None = "d4e5f6a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "strategy_governance",
        "strategy_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )
    op.alter_column(
        "allocation_overrides",
        "strategy_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )
    op.alter_column(
        "fill_quality_metrics",
        "strategy_id",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "fill_quality_metrics",
        "strategy_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "allocation_overrides",
        "strategy_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "strategy_governance",
        "strategy_id",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )

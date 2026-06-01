"""Add missing columns to signals table

Revision ID: jj00kk11ll22
Revises: ii99jj00kk11
Create Date: 2026-06-01 00:00:00.000000

signals ORM model (storage/sor/models/signals.py) defines strategy_version,
feature_snapshot_ref, and target_exposure but these columns were never added
to the DB via migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "jj00kk11ll22"
down_revision = "ii99jj00kk11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signals",
        sa.Column("strategy_version", sa.String(), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("feature_snapshot_ref", sa.String(), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("target_exposure", sa.Numeric(18, 9), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signals", "target_exposure")
    op.drop_column("signals", "feature_snapshot_ref")
    op.drop_column("signals", "strategy_version")

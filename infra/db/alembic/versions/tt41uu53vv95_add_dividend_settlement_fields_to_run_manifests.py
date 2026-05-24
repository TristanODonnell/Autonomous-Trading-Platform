"""add dividend and settlement fields to run_manifests

Revision ID: tt41uu53vv95
Revises: ss40tt52uu84
Create Date: 2026-05-23 00:00:00.000000

Adds settlement_days, dividend_events_count, dividend_events_hash, and
price_adjustment_basis to run_manifests (A-02 / F-06 lineage fields).

All columns are nullable so existing rows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "tt41uu53vv95"
down_revision = "ss40tt52uu84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run_manifests",
        sa.Column("settlement_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column("dividend_events_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column("dividend_events_hash", sa.String(16), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column("price_adjustment_basis", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_manifests", "price_adjustment_basis")
    op.drop_column("run_manifests", "dividend_events_hash")
    op.drop_column("run_manifests", "dividend_events_count")
    op.drop_column("run_manifests", "settlement_days")

"""add settlement fields to cash_snapshots

Revision ID: ss40tt52uu84
Revises: rr39ss51tt83
Create Date: 2026-05-23 00:00:00.000000

Adds settled_cash and unsettled_cash to cash_snapshots (F-06).

Both columns are nullable so existing rows are unaffected (NULL is interpreted
as "all cash is settled / no unsettled cash", preserving legacy semantics).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ss40tt52uu84"
down_revision = "rr39ss51tt83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cash_snapshots",
        sa.Column("settled_cash", sa.Numeric(precision=20, scale=6), nullable=True),
    )
    op.add_column(
        "cash_snapshots",
        sa.Column("unsettled_cash", sa.Numeric(precision=20, scale=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cash_snapshots", "unsettled_cash")
    op.drop_column("cash_snapshots", "settled_cash")

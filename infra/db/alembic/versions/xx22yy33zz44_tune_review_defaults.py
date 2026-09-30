"""raise the portfolio review swap streak and minimum tenure defaults

Portfolio rotation step 5: on the 6-month rotation sweep every trend favoured rotating
less; the swap streak default goes 3 -> 4 consecutive weekly reviews and the minimum
incumbent tenure 30 -> 60 days. Only the server defaults change (existing settings rows
keep their values).

Revision ID: xx22yy33zz44
Revises: ww11xx22yy33
Create Date: 2026-09-30

"""

from __future__ import annotations

from alembic import op

revision = "xx22yy33zz44"
down_revision = "ww11xx22yy33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("operator_settings", "review_swap_consecutive", server_default="4")
    op.alter_column("operator_settings", "review_min_tenure_days", server_default="60")


def downgrade() -> None:
    op.alter_column("operator_settings", "review_min_tenure_days", server_default="30")
    op.alter_column("operator_settings", "review_swap_consecutive", server_default="3")

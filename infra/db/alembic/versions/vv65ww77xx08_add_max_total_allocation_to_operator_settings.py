"""Add max_total_strategy_allocation_pct to operator_settings

Revision ID: vv65ww77xx08
Revises: uu54vv66ww07
Create Date: 2026-05-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "vv65ww77xx08"
down_revision: str | None = "uu54vv66ww07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "max_total_strategy_allocation_pct",
            sa.Numeric(6, 4),
            nullable=True,
            server_default="1.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "max_total_strategy_allocation_pct")

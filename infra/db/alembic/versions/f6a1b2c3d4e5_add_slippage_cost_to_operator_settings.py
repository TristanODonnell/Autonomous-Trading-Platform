"""Add slippage_model and transaction_cost_model to operator_settings

Revision ID: f6a1b2c3d4e5
Revises: e5f6a1b2c3d4
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a1b2c3d4e5"
down_revision: str | None = "e5f6a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column("slippage_model", sa.String(32), nullable=True, server_default="fixed"),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "transaction_cost_model", sa.String(32), nullable=True, server_default="per_share"
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_settings", "slippage_model")
    op.drop_column("operator_settings", "transaction_cost_model")

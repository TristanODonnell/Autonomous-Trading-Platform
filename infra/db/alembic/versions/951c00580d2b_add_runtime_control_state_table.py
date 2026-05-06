"""add runtime control state table

Revision ID: 951c00580d2b
Revises: a7ac640c34dc
Create Date: 2026-05-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "951c00580d2b"
down_revision: str | Sequence[str] | None = "a7ac640c34dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_control_state",
        sa.Column("control_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("trading_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trading_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trading_mode", sa.String(), nullable=False, server_default="paper"),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Insert default global row
    op.execute(
        """
        INSERT INTO runtime_control_state (
            control_id,
            trading_enabled,
            trading_paused,
            kill_switch_enabled,
            trading_mode,
            created_at,
            updated_at
        )
        VALUES (
            'global',
            TRUE,
            FALSE,
            FALSE,
            'paper',
            NOW(),
            NOW()
        )
        """
    )


def downgrade() -> None:
    op.drop_table("runtime_control_state")

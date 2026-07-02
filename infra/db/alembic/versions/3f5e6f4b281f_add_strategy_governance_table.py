"""add strategy governance table

Revision ID: 3f5e6f4b281f
Revises: 67d0d81b6e05
Create Date: 2026-05-05 10:03:35.714510

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3f5e6f4b281f"
down_revision: str | Sequence[str] | None = "67d0d81b6e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "strategy_governance" in sa.inspect(bind).get_table_names():
        op.drop_table("strategy_governance")
    op.create_table(
        "strategy_governance",
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("current_state", sa.String(32), nullable=False),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("source_run_id", sa.UUID(), nullable=True),
        sa.Column("submitted_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("submitted_by", sa.String(64), nullable=False, server_default="system"),
        sa.PrimaryKeyConstraint("strategy_id", "config_hash", name="pk_strategy_governance"),
    )


def downgrade() -> None:
    op.drop_table("strategy_governance")

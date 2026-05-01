"""add strategy_governance table

Revision ID: 589ae84a18f2
Revises: e2dd52335906
Create Date: 2026-05-01 15:49:09.195683

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "589ae84a18f2"
down_revision: str | Sequence[str] | None = "e2dd52335906"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_governance",
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("current_state", sa.String(length=32), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("strategy_id", "config_hash", name=op.f("pk_strategy_governance")),
    )


def downgrade() -> None:
    op.drop_table("strategy_governance")

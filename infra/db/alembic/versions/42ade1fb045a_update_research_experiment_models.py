"""update research experiment models

Revision ID: 42ade1fb045a
Revises: 8af2897c5784
Create Date: 2026-04-29 14:57:32.889427

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "42ade1fb045a"
down_revision: str | Sequence[str] | None = "8af2897c5784"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("strategy_set_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "experiments",
        sa.Column("parameter_grid_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("experiments", sa.Column("dataset_version", sa.String(length=64), nullable=True))
    op.add_column("experiments", sa.Column("universe_version", sa.String(length=64), nullable=True))
    op.add_column("experiments", sa.Column("start_time", sa.DateTime(timezone=True), nullable=True))
    op.add_column("experiments", sa.Column("end_time", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("experiments", "end_time")
    op.drop_column("experiments", "start_time")
    op.drop_column("experiments", "universe_version")
    op.drop_column("experiments", "dataset_version")
    op.drop_column("experiments", "parameter_grid_json")
    op.drop_column("experiments", "strategy_set_json")

"""add additional columns to simulation runs

Revision ID: 72bcc9eb202b
Revises: 44f3da085c13
Create Date: 2026-04-29 16:20:32.297978

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "72bcc9eb202b"
down_revision: str | Sequence[str] | None = "44f3da085c13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "simulation_runs",
        sa.Column("price_basis", sa.String(16), nullable=False, server_default="ADJUSTED"),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("symbols", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("start_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
    )
    op.add_column(
        "simulation_runs",
        sa.Column("end_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
    )
    op.add_column("simulation_runs", sa.Column("window_role", sa.String(32), nullable=True))

    op.alter_column("simulation_runs", "price_basis", server_default=None)
    op.alter_column("simulation_runs", "symbols", server_default=None)
    op.alter_column("simulation_runs", "start_date", server_default=None)
    op.alter_column("simulation_runs", "end_date", server_default=None)


def downgrade() -> None:
    op.drop_column("simulation_runs", "window_role")
    op.drop_column("simulation_runs", "end_date")
    op.drop_column("simulation_runs", "start_date")
    op.drop_column("simulation_runs", "symbols")
    op.drop_column("simulation_runs", "price_basis")

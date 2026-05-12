"""extend runtime_job_runs error_message to TEXT

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-05-12 00:00:01.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        "runtime_job_runs",
        "error_message",
        type_=sa.Text(),
        existing_type=sa.String(2048),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "runtime_job_runs",
        "error_message",
        type_=sa.String(2048),
        existing_type=sa.Text(),
        existing_nullable=True,
    )

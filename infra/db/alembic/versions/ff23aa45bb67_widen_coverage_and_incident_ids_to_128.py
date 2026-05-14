"""widen symbol_date_coverages and missing_bar_incidents primary key columns to VARCHAR(128)

Revision ID: ff23aa45bb67
Revises: ee12ff34aa56
Create Date: 2026-05-14 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ff23aa45bb67"
down_revision: str | Sequence[str] | None = "ee12ff34aa56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "symbol_date_coverages",
        "coverage_id",
        type_=sa.String(128),
        existing_type=sa.String(64),
    )
    op.alter_column(
        "missing_bar_incidents",
        "incident_id",
        type_=sa.String(128),
        existing_type=sa.String(64),
    )


def downgrade() -> None:
    op.alter_column(
        "missing_bar_incidents",
        "incident_id",
        type_=sa.String(64),
        existing_type=sa.String(128),
    )
    op.alter_column(
        "symbol_date_coverages",
        "coverage_id",
        type_=sa.String(64),
        existing_type=sa.String(128),
    )

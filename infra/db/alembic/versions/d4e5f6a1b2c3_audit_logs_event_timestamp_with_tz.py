"""audit_logs event_timestamp to TIMESTAMP WITH TIME ZONE

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-05-11 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a1b2c3"
down_revision: str | Sequence[str] | None = "c3d4e5f6a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.alter_column(
        "audit_logs",
        "event_timestamp",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(timezone=False),
        existing_nullable=False,
        postgresql_using="event_timestamp AT TIME ZONE 'UTC'",
    )


def downgrade():
    op.alter_column(
        "audit_logs",
        "event_timestamp",
        type_=sa.DateTime(timezone=False),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="event_timestamp AT TIME ZONE 'UTC'",
    )

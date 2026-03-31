"""add audit_logs table (fix)

Revision ID: fefc34170e4e
Revises: fbc5d464a623
Create Date: 2026-03-30 19:01:53.422594

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fefc34170e4e"
down_revision: str | Sequence[str] | None = "fbc5d464a623"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade():
    op.create_table(
        "audit_logs",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("event_timestamp", sa.DateTime(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass

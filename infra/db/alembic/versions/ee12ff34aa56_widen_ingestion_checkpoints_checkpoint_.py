"""widen ingestion_checkpoints checkpoint_id to VARCHAR(128)

Revision ID: ee12ff34aa56
Revises: dd01ee23ff45
Create Date: 2026-05-13 19:30:18.560913

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ee12ff34aa56"
down_revision: str | Sequence[str] | None = "dd01ee23ff45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ingestion_checkpoints",
        "checkpoint_id",
        type_=sa.String(128),
        existing_type=sa.String(64),
    )


def downgrade() -> None:
    op.alter_column(
        "ingestion_checkpoints",
        "checkpoint_id",
        type_=sa.String(64),
        existing_type=sa.String(128),
    )

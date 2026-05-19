"""add generation_metadata_json to universe_versions

Revision ID: nn01oo13pp45
Revises: mm90nn12oo34
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "nn01oo13pp45"
down_revision: str | Sequence[str] | None = "mm90nn12oo34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in bind.dialect.get_columns(bind, "universe_versions")}
    if "generation_metadata_json" not in existing_columns:
        op.add_column(
            "universe_versions",
            sa.Column(
                "generation_metadata_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )


def downgrade() -> None:
    op.drop_column("universe_versions", "generation_metadata_json")

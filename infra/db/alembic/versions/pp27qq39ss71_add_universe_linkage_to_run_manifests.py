"""add universe linkage fields to run_manifests

Revision ID: pp27qq39ss71
Revises: oo14pp26rr58
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pp27qq39ss71"
down_revision: str | Sequence[str] | None = "oo14pp26rr58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_manifests", sa.Column("universe_version_id", sa.String(), nullable=True))
    op.add_column("run_manifests", sa.Column("universe_source", sa.String(), nullable=True))
    op.add_column("run_manifests", sa.Column("universe_member_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_manifests", "universe_member_count")
    op.drop_column("run_manifests", "universe_source")
    op.drop_column("run_manifests", "universe_version_id")

"""add universe resolution fields to run_manifests

Revision ID: rr39ss51tt83
Revises: qq28rr40tt82
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "rr39ss51tt83"
down_revision: str | Sequence[str] | None = "qq28rr40tt82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_manifests",
        sa.Column("universe_resolution_mode", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column("universe_effective_timestamp", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "run_manifests",
        sa.Column(
            "replay_rotation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_manifests", "replay_rotation_enabled")
    op.drop_column("run_manifests", "universe_effective_timestamp")
    op.drop_column("run_manifests", "universe_resolution_mode")

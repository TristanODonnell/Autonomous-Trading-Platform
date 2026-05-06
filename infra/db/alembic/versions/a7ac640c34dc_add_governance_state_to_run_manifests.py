"""add governance state to run manifests

Revision ID: a7ac640c34dc
Revises: 857ff324e54a
Create Date: 2026-05-05 17:48:21.696138
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7ac640c34dc"
down_revision: str | Sequence[str] | None = "857ff324e54a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


governance_state_enum = sa.Enum(
    "PROPOSED",
    "APPROVED_RESEARCH",
    "APPROVED_PAPER",
    "APPROVED_LIVE",
    "REJECTED",
    "RETIRED",
    name="governance_state_enum",
)


def upgrade() -> None:
    governance_state_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "run_manifests",
        sa.Column(
            "governance_state",
            governance_state_enum,
            nullable=False,
            server_default="APPROVED_PAPER",
        ),
    )

    op.alter_column(
        "run_manifests",
        "governance_state",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("run_manifests", "governance_state")

    governance_state_enum.drop(op.get_bind(), checkfirst=True)

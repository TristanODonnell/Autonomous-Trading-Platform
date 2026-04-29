"""add price basis to run manifests

Revision ID: d7e129a4cae3
Revises: 59b23d6f9b58
Create Date: 2026-04-28 18:41:17.828565

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e129a4cae3"
down_revision: str | Sequence[str] | None = "59b23d6f9b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "run_manifests",
        sa.Column(
            "price_basis",
            sa.Enum("RAW", "ADJUSTED", name="price_basis_enum"),
            nullable=True,
        ),
    )

    op.execute("UPDATE run_manifests SET price_basis = 'RAW' WHERE price_basis IS NULL")

    op.alter_column("run_manifests", "price_basis", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("run_manifests", "price_basis")

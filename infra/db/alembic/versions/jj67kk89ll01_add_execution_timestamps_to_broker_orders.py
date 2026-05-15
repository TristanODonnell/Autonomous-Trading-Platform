"""add execution timestamps to broker_orders

Revision ID: jj67kk89ll01
Revises: ii56jj78kk90
Create Date: 2026-05-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "jj67kk89ll01"
down_revision: str | Sequence[str] | None = "ii56jj78kk90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    columns = bind.dialect.get_columns(bind, table_name, schema=None)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    for column_name in (
        "signal_generated_at",
        "submitted_to_broker_at",
        "broker_acknowledged_at",
        "first_fill_at",
    ):
        if not _has_column("broker_orders", column_name):
            op.add_column(
                "broker_orders",
                sa.Column(column_name, sa.DateTime(timezone=True), nullable=True),
            )


def downgrade() -> None:
    for column_name in (
        "first_fill_at",
        "broker_acknowledged_at",
        "submitted_to_broker_at",
        "signal_generated_at",
    ):
        if _has_column("broker_orders", column_name):
            op.drop_column("broker_orders", column_name)

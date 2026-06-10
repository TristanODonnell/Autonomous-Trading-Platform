"""add strategy_runtime_states

Revision ID: 8943572f1891
Revises: fefc34170e4e
Create Date: 2026-03-30 19:06:25.236887
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType

revision: str = "8943572f1891"
down_revision: str | Sequence[str] | None = "fefc34170e4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "strategy_runtime_states" in sa.inspect(bind).get_table_names():
        op.drop_table("strategy_runtime_states")
    sa.Enum(name="strategy_runtime_state_enum").drop(bind, checkfirst=True)
    op.create_table(
        "strategy_runtime_states",
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column(
            "current_state",
            sa.Enum(
                "IDLE",
                "SIGNALLED",
                "PENDING",
                "IN_POSITION",
                "EXIT_PENDING",
                "COOLDOWN",
                name="strategy_runtime_state_enum",
            ),
            nullable=False,
        ),
        sa.Column("last_signal_at", UTCDateTimeType(timezone=True), nullable=True),
        sa.Column("last_transition_at", UTCDateTimeType(timezone=True), nullable=False),
        sa.Column("cooldown_until", UTCDateTimeType(timezone=True), nullable=True),
        sa.Column("updated_at", UTCDateTimeType(timezone=True), nullable=False),
        sa.Column("last_evaluated_bar_timestamp", UTCDateTimeType(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("strategy_id", name=op.f("pk_strategy_runtime_states")),
    )


def downgrade() -> None:
    op.drop_table("strategy_runtime_states")

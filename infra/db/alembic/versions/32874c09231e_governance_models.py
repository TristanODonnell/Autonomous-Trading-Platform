from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType

# revision identifiers, used by Alembic.
revision: str = "32874c09231e"
down_revision: str | Sequence[str] | None = "589ae84a18f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "allocation_overrides",
        sa.Column("override_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("overridden_by", sa.String(length=128), nullable=False),
        sa.Column("override_reason", sa.String(length=512), nullable=False),
        sa.Column("max_pct_of_capital", sa.Float(), nullable=True),
        sa.Column("max_position_size_usd", sa.Float(), nullable=True),
        sa.Column("max_drawdown_allowed", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTimeType(), nullable=False),
        sa.Column("expires_at", UTCDateTimeType(), nullable=True),
        sa.PrimaryKeyConstraint("override_id", name=op.f("pk_allocation_overrides")),
    )
    op.create_index(
        "ix_alloc_override_strategy_active",
        "allocation_overrides",
        ["strategy_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "capital_allocation_policies",
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("approval_status", sa.String(length=64), nullable=False),
        sa.Column("performance_tier", sa.String(length=32), nullable=True),
        sa.Column("max_pct_of_capital", sa.Float(), nullable=False),
        sa.Column("max_position_size_usd", sa.Float(), nullable=True),
        sa.Column("max_drawdown_allowed", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTimeType(), nullable=False),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("policy_id", name=op.f("pk_capital_allocation_policies")),
        sa.UniqueConstraint(
            "approval_status", "performance_tier", name="uq_cap_policy_status_tier"
        ),
    )
    op.create_index(
        "ix_cap_policy_status_active",
        "capital_allocation_policies",
        ["approval_status", "is_active"],
        unique=False,
    )

    op.create_table(
        "promotion_rules",
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=64), nullable=False),
        sa.Column("to_status", sa.String(length=64), nullable=False),
        sa.Column("min_sharpe", sa.Float(), nullable=True),
        sa.Column("max_drawdown", sa.Float(), nullable=True),
        sa.Column("min_days_tested", sa.Integer(), nullable=True),
        sa.Column("min_trade_count", sa.Integer(), nullable=True),
        sa.Column("min_cagr", sa.Float(), nullable=True),
        sa.Column("min_win_rate", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTimeType(), nullable=False),
        sa.Column("notes", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("rule_id", name=op.f("pk_promotion_rules")),
    )
    op.create_index(
        "ix_promotion_rules_transition",
        "promotion_rules",
        ["from_status", "to_status", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_promotion_rules_transition", table_name="promotion_rules")
    op.drop_table("promotion_rules")

    op.drop_index("ix_cap_policy_status_active", table_name="capital_allocation_policies")
    op.drop_table("capital_allocation_policies")

    op.drop_index("ix_alloc_override_strategy_active", table_name="allocation_overrides")
    op.drop_table("allocation_overrides")

"""add fill quality metrics

Revision ID: 67d0d81b6e05
Revises: 32874c09231e
Create Date: 2026-05-03 18:35:08.702746

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "67d0d81b6e05"
down_revision: str | Sequence[str] | None = "32874c09231e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fill_quality_metrics",
        # Identity
        sa.Column("record_id", UUID(as_uuid=False), primary_key=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=False), nullable=False),
        sa.Column("intent_id", UUID(as_uuid=False), nullable=False),
        sa.Column("fill_id", sa.String(128), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        # Timestamps
        sa.Column("signal_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submission_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submission_latency_seconds", sa.Float, nullable=False),
        sa.Column("fill_latency_seconds", sa.Float, nullable=True),
        # Price quality — NULLable because fill actuals arrive in phase 2
        sa.Column("reference_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("expected_fill_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("slippage_per_share", sa.Numeric(18, 6), nullable=True),
        sa.Column("slippage_notional", sa.Numeric(18, 6), nullable=True),
        sa.Column("slippage_bps", sa.Numeric(10, 4), nullable=True),
        sa.Column("fill_vs_expected_bps", sa.Numeric(10, 4), nullable=True),
        # Cost
        sa.Column("commission_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("spread_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("slippage_cost", sa.Numeric(18, 6), nullable=True),
        sa.Column("total_cost", sa.Numeric(18, 6), nullable=True),
        # Execution policy context
        sa.Column("policy_mode", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=True),
        sa.Column("is_adverse_fill", sa.Boolean, nullable=True),
        # Flex metadata
        sa.Column("policy_metadata_json", JSONB, nullable=True),
    )

    # Indexes for the most common query patterns:
    #   - look up by intent (reconciliation update)
    #   - filter by run, strategy, symbol for dashboards
    op.create_index("ix_fqm_intent_id", "fill_quality_metrics", ["intent_id"], unique=True)
    op.create_index("ix_fqm_run_id", "fill_quality_metrics", ["run_id"])
    op.create_index("ix_fqm_strategy_id", "fill_quality_metrics", ["strategy_id"])
    op.create_index("ix_fqm_symbol", "fill_quality_metrics", ["symbol"])
    op.create_index(
        "ix_fqm_strategy_symbol",
        "fill_quality_metrics",
        ["strategy_id", "symbol"],
    )


def downgrade() -> None:
    op.drop_index("ix_fqm_strategy_symbol", table_name="fill_quality_metrics")
    op.drop_index("ix_fqm_symbol", table_name="fill_quality_metrics")
    op.drop_index("ix_fqm_strategy_id", table_name="fill_quality_metrics")
    op.drop_index("ix_fqm_run_id", table_name="fill_quality_metrics")
    op.drop_index("ix_fqm_intent_id", table_name="fill_quality_metrics")
    op.drop_table("fill_quality_metrics")

"""add promotion rules live-capital safety constraints

Revision ID: zz09aa21bb32
Revises: xx87yy99zz10
Create Date: 2026-05-26 00:00:00.000000

Design note:
Transition-specific constraints (only for to_status='approved_live') cannot be expressed
as simple NOT NULL column constraints because the PromotionRules table serves multiple
transitions.  We use conditional CHECK constraints instead:

  - ck_prom_rules_live_min_days:
    If to_status = 'approved_live' then min_days_tested must be >= 30.
    Service-layer validation (_REQUIRED_CRITERIA_BY_TRANSITION) enforces non-null; this
    constraint prevents misconfigured values that satisfy non-null but are still unsafe.

  - ck_prom_rules_live_min_trade_count:
    If to_status = 'approved_live' then min_trade_count must be > 0.

SQLite enforces CHECK constraints since 3.25.0 (Python's bundled SQLite meets this).
PostgreSQL enforces them unconditionally.

These constraints are intentionally lenient about NULL so they do not conflict with
rows for other transitions (approved_research -> approved_paper) that leave these
fields null.  Service-layer PromotionCriteriaConfigurationError is the primary
enforcement mechanism for the null case.
"""

from __future__ import annotations

from alembic import op

revision = "zz09aa21bb32"
down_revision = "xx87yy99zz10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("promotion_rules") as batch_op:
        batch_op.create_check_constraint(
            "ck_prom_rules_live_min_days",
            "to_status != 'approved_live' OR min_days_tested IS NULL OR min_days_tested >= 30",
        )
        batch_op.create_check_constraint(
            "ck_prom_rules_live_min_trade_count",
            "to_status != 'approved_live' OR min_trade_count IS NULL OR min_trade_count > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("promotion_rules") as batch_op:
        batch_op.drop_constraint("ck_prom_rules_live_min_trade_count")
        batch_op.drop_constraint("ck_prom_rules_live_min_days")

"""Add strategy health lifecycle tables and columns

Revision ID: ee55ff66gg77
Revises: dd44ee55ff66
Create Date: 2026-05-28 00:00:00.000000

Design note:
Implements Recommendation 6.3 — Strategy Health Lifecycle Separate from
Governance State.

Changes:
  1. strategy_health_states: add lifecycle columns for anti-flapping
     (cooldown_expires_at), suspension (suspended_at, suspension_reason,
     operator_review_required), allocation penalty (allocation_penalty),
     critical escalation tracking (consecutive_critical_count), and
     lifecycle evaluation mode (lifecycle_mode).
  2. strategy_health_transitions: new append-only audit table recording
     every health state transition with full metric snapshot and provenance.

All new columns on strategy_health_states are nullable or have safe defaults
so existing rows remain valid without a data migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ee55ff66gg77"
down_revision = ("dd44ee55ff66", "aa10bb22cc43")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. strategy_health_states — add lifecycle columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("strategy_health_states") as batch_op:
        batch_op.add_column(sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("suspension_reason", sa.String(512), nullable=True))
        batch_op.add_column(
            sa.Column(
                "operator_review_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch_op.add_column(
            sa.Column("cooldown_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "consecutive_critical_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(sa.Column("allocation_penalty", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("lifecycle_mode", sa.String(32), nullable=True))
        batch_op.create_index(
            "ix_shs_operator_review_required",
            ["operator_review_required"],
        )

    # ------------------------------------------------------------------
    # 2. strategy_health_transitions — new append-only audit table
    # ------------------------------------------------------------------
    op.create_table(
        "strategy_health_transitions",
        sa.Column("transition_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("transition_reason", sa.String(1024), nullable=True),
        sa.Column(
            "triggered_by",
            sa.String(256),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column(
            "triggering_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("drawdown_utilization", sa.Float(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("rolling_sharpe", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("allocation_penalty_after", sa.Float(), nullable=True),
        sa.Column("cooldown_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_run_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("transition_id"),
    )
    op.create_index(
        "ix_sht_strategy_id",
        "strategy_health_transitions",
        ["strategy_id"],
    )
    op.create_index(
        "ix_sht_created_at",
        "strategy_health_transitions",
        ["created_at"],
    )
    op.create_index(
        "ix_sht_to_status",
        "strategy_health_transitions",
        ["to_status"],
    )
    op.create_index(
        "ix_sht_strategy_created",
        "strategy_health_transitions",
        ["strategy_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sht_strategy_created", table_name="strategy_health_transitions")
    op.drop_index("ix_sht_to_status", table_name="strategy_health_transitions")
    op.drop_index("ix_sht_created_at", table_name="strategy_health_transitions")
    op.drop_index("ix_sht_strategy_id", table_name="strategy_health_transitions")
    op.drop_table("strategy_health_transitions")

    with op.batch_alter_table("strategy_health_states") as batch_op:
        batch_op.drop_index("ix_shs_operator_review_required")
        batch_op.drop_column("lifecycle_mode")
        batch_op.drop_column("allocation_penalty")
        batch_op.drop_column("consecutive_critical_count")
        batch_op.drop_column("cooldown_expires_at")
        batch_op.drop_column("operator_review_required")
        batch_op.drop_column("suspension_reason")
        batch_op.drop_column("suspended_at")

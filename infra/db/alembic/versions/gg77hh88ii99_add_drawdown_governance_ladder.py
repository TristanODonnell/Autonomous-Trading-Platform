"""Add drawdown governance ladder tables

Revision ID: gg77hh88ii99
Revises: ff66gg77hh88
Create Date: 2026-05-28 00:00:00.000000

Design note:
Implements Recommendation 6.6 — Drawdown Governance Ladder.

Replaces the binary drawdown enforcement model with a progressive governance
ladder that scales down strategy allocation as drawdown utilization increases:

  NORMAL     (utilization < 50%)  → allocation_scalar = 1.00
  WARNING    (utilization 50–75%) → allocation_scalar = 0.50
  PROBATION  (utilization 75–90%) → allocation_scalar = 0.25
  SUSPENDED  (utilization 90–100%)→ allocation_scalar = 0.00
  BREACHED   (utilization > 100%) → allocation_scalar = 0.00 + governance escalation

Creates two tables:

  1. drawdown_governance_ladder_states   — per-strategy current rung state
  2. drawdown_governance_ladder_transitions — append-only transition audit trail

No existing tables are modified.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "gg77hh88ii99"
down_revision = "ff66gg77hh88"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. drawdown_governance_ladder_states  (current per-strategy rung)
    # ------------------------------------------------------------------
    op.create_table(
        "drawdown_governance_ladder_states",
        sa.Column("ladder_state_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(128), nullable=False, unique=True),
        # Ladder position
        sa.Column(
            "ladder_state", sa.String(32), nullable=False, server_default=sa.text("'normal'")
        ),
        sa.Column("previous_ladder_state", sa.String(32), nullable=True),
        sa.Column("transition_reason", sa.String(512), nullable=True),
        # Drawdown metrics
        sa.Column("realized_drawdown", sa.Float(), nullable=True),
        sa.Column("max_drawdown_allowed", sa.Float(), nullable=True),
        sa.Column("drawdown_utilization", sa.Float(), nullable=True),
        # Allocation scalar at the current rung
        sa.Column("allocation_scalar", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        # Anti-flapping
        sa.Column("cooldown_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Operator acknowledgement (for breach recovery)
        sa.Column(
            "operator_ack_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("operator_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator_acknowledged_by", sa.String(256), nullable=True),
        # Timestamps
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ddg_ladder_state_strategy_id",
        "drawdown_governance_ladder_states",
        ["strategy_id"],
    )
    op.create_index(
        "ix_ddg_ladder_state_ladder_state",
        "drawdown_governance_ladder_states",
        ["ladder_state"],
    )
    op.create_index(
        "ix_ddg_ladder_state_operator_ack_required",
        "drawdown_governance_ladder_states",
        ["operator_ack_required"],
    )

    # ------------------------------------------------------------------
    # 2. drawdown_governance_ladder_transitions  (append-only audit trail)
    # ------------------------------------------------------------------
    op.create_table(
        "drawdown_governance_ladder_transitions",
        sa.Column("transition_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=True),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("transition_reason", sa.String(1024), nullable=True),
        sa.Column(
            "triggered_by", sa.String(256), nullable=False, server_default=sa.text("'system'")
        ),
        # Drawdown context at transition time
        sa.Column("realized_drawdown", sa.Float(), nullable=True),
        sa.Column("max_drawdown_allowed", sa.Float(), nullable=True),
        sa.Column("drawdown_utilization", sa.Float(), nullable=True),
        sa.Column("allocation_scalar_after", sa.Float(), nullable=True),
        sa.Column("cooldown_expires_at", sa.DateTime(timezone=True), nullable=True),
        # Snapshot of triggering metrics (JSONB on PG, JSON on SQLite)
        sa.Column("triggering_metrics", postgresql.JSONB(), nullable=True),
        # Link back to evaluation run
        sa.Column("evaluation_run_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ddg_trans_strategy_id",
        "drawdown_governance_ladder_transitions",
        ["strategy_id"],
    )
    op.create_index(
        "ix_ddg_trans_created_at",
        "drawdown_governance_ladder_transitions",
        ["created_at"],
    )
    op.create_index(
        "ix_ddg_trans_to_state",
        "drawdown_governance_ladder_transitions",
        ["to_state"],
    )
    op.create_index(
        "ix_ddg_trans_strategy_created",
        "drawdown_governance_ladder_transitions",
        ["strategy_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("drawdown_governance_ladder_transitions")
    op.drop_table("drawdown_governance_ladder_states")

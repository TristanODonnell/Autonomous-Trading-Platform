"""Create governance_audit_events table

Revision ID: kk11ll22mm33
Revises: jj00kk11ll22
Create Date: 2026-06-01 00:00:00.000000

governance_audit_events ORM model exists in
storage/sor/models/governance_audit_events.py but the table was never
created via migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "kk11ll22mm33"
down_revision = "jj00kk11ll22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_audit_events",
        sa.Column("governance_audit_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("trigger_source", sa.String(64), nullable=False),
        sa.Column("transition_from", sa.String(64), nullable=True),
        sa.Column("transition_to", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("decision_outcome", sa.String(32), nullable=False),
        sa.Column("decision_rationale", sa.Text(), nullable=True),
        sa.Column("criteria_evaluated", postgresql.JSONB(), nullable=True),
        sa.Column("source_run_id", sa.String(128), nullable=True),
        sa.Column("simulation_run_id", sa.String(128), nullable=True),
        sa.Column("experiment_run_id", sa.String(128), nullable=True),
        sa.Column("allocation_config_hash", sa.String(128), nullable=True),
        sa.Column("covariance_snapshot_id", sa.String(128), nullable=True),
        sa.Column("factor_snapshot_id", sa.String(128), nullable=True),
        sa.Column("metrics_lineage", postgresql.JSONB(), nullable=True),
        sa.Column("state_snapshot_before", postgresql.JSONB(), nullable=True),
        sa.Column("state_snapshot_after", postgresql.JSONB(), nullable=True),
        sa.Column("promotion_rule_version", sa.String(64), nullable=True),
        sa.Column("governance_policy_version", sa.String(64), nullable=True),
        sa.Column("evaluation_version", sa.String(64), nullable=True),
        sa.Column("shadow_validation_status", sa.String(64), nullable=True),
        sa.Column("shadow_divergence_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("superseded_by", sa.String(64), nullable=True),
        sa.Column("evaluation_run_id", sa.String(128), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gov_audit_strategy_id", "governance_audit_events", ["strategy_id"])
    op.create_index("ix_gov_audit_trigger_source", "governance_audit_events", ["trigger_source"])
    op.create_index("ix_gov_audit_event_type", "governance_audit_events", ["event_type"])
    op.create_index(
        "ix_gov_audit_decision_outcome", "governance_audit_events", ["decision_outcome"]
    )
    op.create_index("ix_gov_audit_recorded_at", "governance_audit_events", ["recorded_at"])
    op.create_index("ix_gov_audit_source_run_id", "governance_audit_events", ["source_run_id"])
    op.create_index(
        "ix_gov_audit_strategy_recorded",
        "governance_audit_events",
        ["strategy_id", "recorded_at"],
    )
    op.create_index("ix_gov_audit_superseded_by", "governance_audit_events", ["superseded_by"])


def downgrade() -> None:
    op.drop_index("ix_gov_audit_superseded_by", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_strategy_recorded", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_source_run_id", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_recorded_at", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_decision_outcome", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_event_type", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_trigger_source", table_name="governance_audit_events")
    op.drop_index("ix_gov_audit_strategy_id", table_name="governance_audit_events")
    op.drop_table("governance_audit_events")

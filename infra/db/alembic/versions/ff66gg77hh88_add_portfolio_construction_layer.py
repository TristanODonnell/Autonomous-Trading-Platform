"""Add portfolio construction pipeline tables

Revision ID: ff66gg77hh88
Revises: ee55ff66gg77
Create Date: 2026-05-28 00:00:00.000000

Design note:
Implements Recommendation 6.5 — Portfolio Construction Layer with Explicit
Netting.

Creates four tables that persist the artifacts produced at each phase of
the two-phase portfolio construction pipeline:

  1. portfolio_construction_runs — aggregate diagnostics per pipeline run
  2. portfolio_signal_batch_items — raw strategy signals (Phase 1 output)
  3. portfolio_netted_signals — post-aggregation portfolio signals (Phase 2)
  4. portfolio_signal_intents — constraint-gated signal intents (Phase 3)

All tables are append-only.  No existing tables are modified.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ff66gg77hh88"
down_revision = "ee55ff66gg77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. portfolio_construction_runs
    # ------------------------------------------------------------------
    op.create_table(
        "portfolio_construction_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False, unique=True),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("constructed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("netting_policy", sa.String(32), nullable=False),
        sa.Column("strategy_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("symbol_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("raw_signal_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("netted_signal_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("suppressed_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "suppressed_notional_usd", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "constraint_applied_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "constraint_rejected_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "raw_gross_signal_exposure", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("net_signal_exposure", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "exposure_before_constraints", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "exposure_after_constraints", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("final_order_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "construction_duration_seconds", sa.Float(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("diagnostics_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_pc_run_run_id", "portfolio_construction_runs", ["run_id"])
    op.create_index("ix_pc_run_bar_timestamp", "portfolio_construction_runs", ["bar_timestamp"])
    op.create_index("ix_pc_run_constructed_at", "portfolio_construction_runs", ["constructed_at"])

    # ------------------------------------------------------------------
    # 2. portfolio_signal_batch_items
    # ------------------------------------------------------------------
    op.create_table(
        "portfolio_signal_batch_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("signal_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_version", sa.String(64), nullable=True),
        sa.Column("feature_snapshot_ref", sa.String(128), nullable=True),
    )
    op.create_index("ix_pc_batch_item_batch_id", "portfolio_signal_batch_items", ["batch_id"])
    op.create_index("ix_pc_batch_item_run_id", "portfolio_signal_batch_items", ["run_id"])
    op.create_index("ix_pc_batch_item_strategy_id", "portfolio_signal_batch_items", ["strategy_id"])

    # ------------------------------------------------------------------
    # 3. portfolio_netted_signals
    # ------------------------------------------------------------------
    op.create_table(
        "portfolio_netted_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("portfolio_signal_id", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("net_direction", sa.String(16), nullable=False),
        sa.Column("net_confidence", sa.Float(), nullable=False),
        sa.Column("conflict_detected", sa.Boolean(), nullable=False),
        sa.Column("constraint_status", sa.String(16), nullable=False),
        sa.Column("netting_policy", sa.String(32), nullable=False),
        sa.Column("gross_buy_notional", sa.Float(), nullable=True),
        sa.Column("gross_sell_notional", sa.Float(), nullable=True),
        sa.Column("net_notional", sa.Float(), nullable=True),
        sa.Column("contributing_strategy_ids", postgresql.JSONB(), nullable=False),
        sa.Column("attribution_json", postgresql.JSONB(), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pc_netted_batch_id", "portfolio_netted_signals", ["batch_id"])
    op.create_index("ix_pc_netted_run_id", "portfolio_netted_signals", ["run_id"])
    op.create_index("ix_pc_netted_symbol", "portfolio_netted_signals", ["symbol"])

    # ------------------------------------------------------------------
    # 4. portfolio_signal_intents
    # ------------------------------------------------------------------
    op.create_table(
        "portfolio_signal_intents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("intent_id", sa.String(64), nullable=False),
        sa.Column("portfolio_signal_id", sa.String(64), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("constraint_status", sa.String(16), nullable=False),
        sa.Column("constraints_triggered", postgresql.JSONB(), nullable=False),
        sa.Column("attribution_json", postgresql.JSONB(), nullable=False),
        sa.Column("bar_timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pc_intent_batch_id", "portfolio_signal_intents", ["batch_id"])
    op.create_index("ix_pc_intent_run_id", "portfolio_signal_intents", ["run_id"])
    op.create_index("ix_pc_intent_symbol", "portfolio_signal_intents", ["symbol"])
    op.create_index("ix_pc_intent_status", "portfolio_signal_intents", ["constraint_status"])


def downgrade() -> None:
    op.drop_table("portfolio_signal_intents")
    op.drop_table("portfolio_netted_signals")
    op.drop_table("portfolio_signal_batch_items")
    op.drop_table("portfolio_construction_runs")

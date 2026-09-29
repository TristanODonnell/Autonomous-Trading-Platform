"""add portfolio review settings, reviews, scorecards and decisions

Portfolio rotation step 4A:

  operator_settings                 portfolio_review_mode (default off) and the review
                                    guardrails (swap margin, consecutive reviews, tenure,
                                    swap cap and interval, turnover cost, shadow minimum,
                                    score floor, on-deck tenure)
  portfolio_reviews                 one row per review
  portfolio_scorecards              one row per (review, strategy): evidence, weights,
                                    lens penalties, score, rank
  portfolio_review_decisions        one row per decision, with guardrail results
  portfolio_membership_transitions  review_id links a membership change to its review

Revision ID: ww11xx22yy33
Revises: vv00ww11xx22
Create Date: 2026-09-28

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ww11xx22yy33"
down_revision = "vv00ww11xx22"
branch_labels = None
depends_on = None

_SETTINGS_COLUMNS = [
    sa.Column("portfolio_review_mode", sa.String(16), nullable=False, server_default="off"),
    sa.Column("review_swap_margin", sa.Numeric(6, 4), nullable=False, server_default="0.10"),
    sa.Column("review_swap_consecutive", sa.Integer(), nullable=False, server_default="3"),
    sa.Column("review_min_tenure_days", sa.Integer(), nullable=False, server_default="30"),
    sa.Column("review_max_swaps_per_review", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("review_swap_interval_days", sa.Integer(), nullable=False, server_default="28"),
    sa.Column("review_turnover_cost_bps", sa.Numeric(8, 2), nullable=False, server_default="20"),
    sa.Column("review_min_shadow_days", sa.Integer(), nullable=False, server_default="20"),
    sa.Column("review_min_shadow_trades", sa.Integer(), nullable=False, server_default="10"),
    sa.Column("review_score_floor", sa.Numeric(8, 4), nullable=False, server_default="1.0"),
    sa.Column("review_on_deck_min_tenure_days", sa.Integer(), nullable=False, server_default="21"),
]


def upgrade() -> None:
    for column in _SETTINGS_COLUMNS:
        op.add_column("operator_settings", column)

    op.add_column(
        "portfolio_membership_transitions",
        sa.Column("review_id", sa.String(64), nullable=True),
    )

    op.create_table(
        "portfolio_reviews",
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("swap_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("bench_review_id", sa.String(64), nullable=True),
        sa.Column("scorecard_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("review_id", name="pk_portfolio_reviews"),
    )
    op.create_index("ix_portfolio_reviews_reviewed_at", "portfolio_reviews", ["reviewed_at"])

    op.create_table(
        "portfolio_scorecards",
        sa.Column("scorecard_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("strategy_type", sa.String(64), nullable=True),
        sa.Column("forward_source", sa.String(16), nullable=True),
        sa.Column("forward_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("forward_weight", sa.Numeric(8, 6), nullable=False, server_default="0"),
        sa.Column("forward_days", sa.Integer(), nullable=True),
        sa.Column("forward_trades", sa.Integer(), nullable=True),
        sa.Column("resim_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("resim_weight", sa.Numeric(8, 6), nullable=False, server_default="0"),
        sa.Column("backtest_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("backtest_weight", sa.Numeric(8, 6), nullable=False, server_default="0"),
        sa.Column("backtest_age_days", sa.Integer(), nullable=True),
        sa.Column("evidence_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("decay_penalty", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("health_status", sa.String(32), nullable=True),
        sa.Column("health_penalty", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("mean_correlation", sa.Float(), nullable=True),
        sa.Column("correlation_penalty", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("blocked_ratio", sa.Float(), nullable=True),
        sa.Column("blocked_penalty", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("regime_label", sa.String(64), nullable=True),
        sa.Column("score", sa.Numeric(12, 6), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("scorecard_id", name="pk_portfolio_scorecards"),
    )
    op.create_index("ix_portfolio_scorecards_review", "portfolio_scorecards", ["review_id"])
    op.create_index(
        "ix_portfolio_scorecards_strategy_reviewed",
        "portfolio_scorecards",
        ["strategy_id", "reviewed_at"],
    )

    op.create_table(
        "portfolio_review_decisions",
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_type", sa.String(32), nullable=False),
        sa.Column("strategy_id", sa.String(128), nullable=False),
        sa.Column("counterpart_id", sa.String(128), nullable=True),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=True),
        sa.Column("strategy_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("counterpart_score", sa.Numeric(12, 6), nullable=True),
        sa.Column("margin", sa.Float(), nullable=True),
        sa.Column("streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("guardrails", postgresql.JSONB(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("decision_id", name="pk_portfolio_review_decisions"),
    )
    op.create_index(
        "ix_portfolio_review_decisions_review", "portfolio_review_decisions", ["review_id"]
    )
    op.create_index(
        "ix_portfolio_review_decisions_strategy_reviewed",
        "portfolio_review_decisions",
        ["strategy_id", "reviewed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_portfolio_review_decisions_strategy_reviewed",
        table_name="portfolio_review_decisions",
    )
    op.drop_index("ix_portfolio_review_decisions_review", table_name="portfolio_review_decisions")
    op.drop_table("portfolio_review_decisions")
    op.drop_index("ix_portfolio_scorecards_strategy_reviewed", table_name="portfolio_scorecards")
    op.drop_index("ix_portfolio_scorecards_review", table_name="portfolio_scorecards")
    op.drop_table("portfolio_scorecards")
    op.drop_index("ix_portfolio_reviews_reviewed_at", table_name="portfolio_reviews")
    op.drop_table("portfolio_reviews")
    op.drop_column("portfolio_membership_transitions", "review_id")
    for column in reversed(_SETTINGS_COLUMNS):
        op.drop_column("operator_settings", column.name)

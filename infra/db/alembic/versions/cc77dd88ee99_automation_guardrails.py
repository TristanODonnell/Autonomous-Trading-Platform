"""automation guardrails (pre-launch plan 3.1)

Switches and lineage for automated decisions before the worker is connected:

- operator_settings: ``worker_results_enabled`` (accept results posted by the worker),
  ``worker_result_max_age_days`` (reject results built on data older than this) and
  ``worker_result_require_matching_git_sha``. Flippable through the settings API / CLI,
  so no deploy is needed to switch an automation off.
- portfolio_reviews.worker_run_id: which worker run's re-sims a review consumed.
- worker_result_submissions: every result the worker posts, accepted or rejected, keyed by
  the worker run id so the hand-off is idempotent. Feeds the "worker results rejected"
  dashboard panel.

Revision ID: cc77dd88ee99
Revises: bb66cc77dd88
Create Date: 2026-10-12

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "cc77dd88ee99"
down_revision = "bb66cc77dd88"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operator_settings",
        sa.Column(
            "worker_results_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "operator_settings",
        sa.Column("worker_result_max_age_days", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "operator_settings",
        sa.Column(
            "worker_result_require_matching_git_sha",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.add_column("portfolio_reviews", sa.Column("worker_run_id", sa.String(128), nullable=True))
    op.create_index("ix_portfolio_reviews_worker_run_id", "portfolio_reviews", ["worker_run_id"])

    op.create_table(
        "worker_result_submissions",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("result_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rejection_reason", sa.String(128), nullable=True),
        sa.Column("worker_host", sa.String(128), nullable=True),
        sa.Column("git_sha", sa.String(64), nullable=True),
        sa.Column("fixture", sa.String(256), nullable=True),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dataset_as_of", sa.Date(), nullable=True),
        sa.Column("dataset_versions", postgresql.JSONB(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_worker_result_submissions_received_at", "worker_result_submissions", ["received_at"]
    )
    op.create_index("ix_worker_result_submissions_status", "worker_result_submissions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_worker_result_submissions_status", table_name="worker_result_submissions")
    op.drop_index(
        "ix_worker_result_submissions_received_at", table_name="worker_result_submissions"
    )
    op.drop_table("worker_result_submissions")
    op.drop_index("ix_portfolio_reviews_worker_run_id", table_name="portfolio_reviews")
    op.drop_column("portfolio_reviews", "worker_run_id")
    op.drop_column("operator_settings", "worker_result_require_matching_git_sha")
    op.drop_column("operator_settings", "worker_result_max_age_days")
    op.drop_column("operator_settings", "worker_results_enabled")

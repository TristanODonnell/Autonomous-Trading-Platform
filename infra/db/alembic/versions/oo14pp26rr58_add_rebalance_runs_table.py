"""add universe_rebalance_runs table

Revision ID: oo14pp26rr58
Revises: nn01oo13pp45
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "oo14pp26rr58"
down_revision: str | Sequence[str] | None = "nn01oo13pp45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind)
    if "universe_rebalance_runs" in existing_tables:
        return

    op.create_table(
        "universe_rebalance_runs",
        sa.Column("rebalance_run_id", sa.String(), nullable=False),
        sa.Column("previous_universe_version_id", sa.String(), nullable=True),
        sa.Column("candidate_universe_version_id", sa.String(), nullable=False),
        sa.Column("proposed_universe_version_id", sa.String(), nullable=True),
        sa.Column(
            "added_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "removed_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "retained_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("churn_pct", sa.Float(), nullable=True),
        sa.Column("target_universe_size", sa.Integer(), nullable=False),
        sa.Column("max_churn_pct", sa.Float(), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rejection_reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("rebalance_run_id"),
    )
    op.create_index(
        "ix_rebalance_runs_candidate_version_id",
        "universe_rebalance_runs",
        ["candidate_universe_version_id"],
    )
    op.create_index(
        "ix_rebalance_runs_previous_version_id",
        "universe_rebalance_runs",
        ["previous_universe_version_id"],
    )
    op.create_index(
        "ix_rebalance_runs_proposed_version_id",
        "universe_rebalance_runs",
        ["proposed_universe_version_id"],
    )
    op.create_index(
        "ix_rebalance_runs_created_at",
        "universe_rebalance_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rebalance_runs_created_at", table_name="universe_rebalance_runs")
    op.drop_index("ix_rebalance_runs_proposed_version_id", table_name="universe_rebalance_runs")
    op.drop_index("ix_rebalance_runs_previous_version_id", table_name="universe_rebalance_runs")
    op.drop_index("ix_rebalance_runs_candidate_version_id", table_name="universe_rebalance_runs")
    op.drop_table("universe_rebalance_runs")

"""record which corporate action was applied to which book

Rotation step 5d-B: splits and cash dividends are applied to the account book, the
real and shadow sleeves and the research engine through one shared rule; this table is
the idempotency ledger so a restart or replayed tick never applies an action twice.

Revision ID: aa55bb66cc77
Revises: zz44aa55bb66
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "aa55bb66cc77"
down_revision = "zz44aa55bb66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corporate_action_applications",
        sa.Column("application_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("book", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column(
            "action_type",
            postgresql.ENUM(name="corporate_action_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity_before", sa.Numeric(18, 9), nullable=False),
        sa.Column("quantity_after", sa.Numeric(18, 9), nullable=False),
        sa.Column("avg_cost_before", sa.Numeric(18, 6), nullable=True),
        sa.Column("avg_cost_after", sa.Numeric(18, 6), nullable=True),
        sa.Column("cash_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(18, 6), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("action_id", "book", "scope", name="uq_caa_action_book_scope"),
    )
    op.create_index(
        "ix_caa_symbol_effective",
        "corporate_action_applications",
        ["symbol", "effective_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_caa_symbol_effective", table_name="corporate_action_applications")
    op.drop_table("corporate_action_applications")

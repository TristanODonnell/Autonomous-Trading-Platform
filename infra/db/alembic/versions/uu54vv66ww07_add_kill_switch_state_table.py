"""add kill_switch_state table

Revision ID: uu54vv66ww07
Revises: tt41uu53vv95
Create Date: 2026-05-25 00:00:00.000000

Adds a dedicated kill_switch_state singleton table so that kill-switch state
survives service restarts, deploys, and crashes.  The row with id='current'
is the single source of truth; the repository initialises it on first access.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "uu54vv66ww07"
down_revision = "tt41uu53vv95"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kill_switch_state",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.String(), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_kill_switch_state"),
    )


def downgrade() -> None:
    op.drop_table("kill_switch_state")

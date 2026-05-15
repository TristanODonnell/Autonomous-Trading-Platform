"""add universe_versions and universe_members tables

Revision ID: ll89mm01nn23
Revises: kk78ll90mm12
Create Date: 2026-05-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ll89mm01nn23"
down_revision: str | Sequence[str] | None = "kk78ll90mm12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)

    if "universe_versions" not in existing_tables:
        op.create_table(
            "universe_versions",
            sa.Column("universe_version_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("rebalance_reason", sa.String(), nullable=True),
            sa.Column("config_hash", sa.String(), nullable=False),
            sa.PrimaryKeyConstraint("universe_version_id", name=op.f("pk_universe_versions")),
        )
        op.create_index(op.f("ix_universe_versions_status"), "universe_versions", ["status"])
        op.create_index(
            op.f("ix_universe_versions_effective_from"),
            "universe_versions",
            ["effective_from"],
        )
        op.create_index(
            op.f("ix_universe_versions_effective_to"),
            "universe_versions",
            ["effective_to"],
        )

    if "universe_members" not in existing_tables:
        op.create_table(
            "universe_members",
            sa.Column("universe_version_id", sa.String(), nullable=False),
            sa.Column("symbol", sa.String(length=32), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("included_reason", sa.String(), nullable=True),
            sa.Column("excluded_reason", sa.String(), nullable=True),
            sa.Column(
                "liquidity_metrics_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "quality_metrics_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["universe_version_id"],
                ["universe_versions.universe_version_id"],
                name=op.f("fk_universe_members_universe_version_id_universe_versions"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint(
                "universe_version_id", "symbol", name=op.f("pk_universe_members")
            ),
        )
        op.create_index(
            op.f("ix_universe_members_universe_version_id"),
            "universe_members",
            ["universe_version_id"],
        )
        op.create_index(op.f("ix_universe_members_symbol"), "universe_members", ["symbol"])


def downgrade() -> None:
    op.drop_index(op.f("ix_universe_members_symbol"), table_name="universe_members")
    op.drop_index(op.f("ix_universe_members_universe_version_id"), table_name="universe_members")
    op.drop_table("universe_members")

    op.drop_index(op.f("ix_universe_versions_effective_to"), table_name="universe_versions")
    op.drop_index(op.f("ix_universe_versions_effective_from"), table_name="universe_versions")
    op.drop_index(op.f("ix_universe_versions_status"), table_name="universe_versions")
    op.drop_table("universe_versions")

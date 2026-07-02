"""migration schema drift passthrough

Revision ID: a85c2ede6d4e
Revises: 3467a0ff2319
Create Date: 2026-05-09 15:20:14.606725
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "a85c2ede6d4e"
down_revision: str | Sequence[str] | None = "3467a0ff2319"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Align migrated columns with runtime contract/ORM schema drift fixes."""
    if _has_column("fill_quality_metrics", "policy_metadata_json") and not _has_column(
        "fill_quality_metrics", "policy_metadata"
    ):
        op.alter_column(
            "fill_quality_metrics",
            "policy_metadata_json",
            new_column_name="policy_metadata",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=True,
        )

    if _has_column("market_bars", "market_session") and not _has_column("market_bars", "session"):
        op.alter_column(
            "market_bars",
            "market_session",
            new_column_name="session",
            existing_type=postgresql.ENUM(
                "PREMARKET",
                "REGULAR",
                "POSTMARKET",
                "OVERNIGHT",
                name="market_session_enum",
            ),
            existing_nullable=False,
        )

    if _has_column("market_bars", "quality_flags"):
        op.execute("UPDATE market_bars SET quality_flags = '[]'::jsonb WHERE quality_flags IS NULL")
        op.alter_column(
            "market_bars",
            "quality_flags",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        )

    if _has_column("audit_logs", "event_metadata") and not _has_column("audit_logs", "metadata"):
        op.alter_column(
            "audit_logs",
            "event_metadata",
            new_column_name="metadata",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=True,
        )

    if not _has_column("dataset_versions", "source_dataset_version"):
        op.add_column(
            "dataset_versions",
            sa.Column("source_dataset_version", sa.String(length=64), nullable=True),
        )

    if _has_column("dataset_versions", "price_basis"):
        op.execute("UPDATE dataset_versions SET price_basis = 'RAW' WHERE price_basis IS NULL")
        op.alter_column(
            "dataset_versions",
            "price_basis",
            existing_type=postgresql.ENUM("RAW", "ADJUSTED", name="price_basis_enum"),
            nullable=False,
        )

    if _has_column("dataset_versions", "interval"):
        op.execute("UPDATE dataset_versions SET interval = 'ONE_MIN' WHERE interval IS NULL")
        op.alter_column(
            "dataset_versions",
            "interval",
            existing_type=postgresql.ENUM(
                "ONE_MIN",
                "FIVE_MIN",
                "FIFTEEN_MIN",
                "ONE_HOUR",
                "ONE_DAY",
                name="bar_interval_enum",
            ),
            nullable=False,
        )

    if _has_column("corporate_actions", "new_symbol"):
        op.execute("UPDATE corporate_actions SET new_symbol = '' WHERE new_symbol IS NULL")
        op.alter_column(
            "corporate_actions",
            "new_symbol",
            existing_type=sa.String(length=64),
            nullable=False,
        )

    if _has_column("ticker_lifecycle_events", "new_symbol"):
        op.drop_column("ticker_lifecycle_events", "new_symbol")

    if _has_column("ticker_lifecycle_events", "created_at"):
        op.drop_column("ticker_lifecycle_events", "created_at")


def downgrade() -> None:
    """Reverse the runtime contract/ORM schema drift fixes."""
    if not _has_column("ticker_lifecycle_events", "created_at"):
        op.add_column(
            "ticker_lifecycle_events",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            "UPDATE ticker_lifecycle_events SET created_at = effective_at WHERE created_at IS NULL"
        )
        op.alter_column(
            "ticker_lifecycle_events",
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    if not _has_column("ticker_lifecycle_events", "new_symbol"):
        op.add_column(
            "ticker_lifecycle_events",
            sa.Column("new_symbol", sa.String(), nullable=True),
        )

    if _has_column("corporate_actions", "new_symbol"):
        op.alter_column(
            "corporate_actions",
            "new_symbol",
            existing_type=sa.String(length=64),
            nullable=True,
        )

    if _has_column("dataset_versions", "interval"):
        op.alter_column(
            "dataset_versions",
            "interval",
            existing_type=postgresql.ENUM(
                "ONE_MIN",
                "FIVE_MIN",
                "FIFTEEN_MIN",
                "ONE_HOUR",
                "ONE_DAY",
                name="bar_interval_enum",
            ),
            nullable=True,
        )

    if _has_column("dataset_versions", "price_basis"):
        op.alter_column(
            "dataset_versions",
            "price_basis",
            existing_type=postgresql.ENUM("RAW", "ADJUSTED", name="price_basis_enum"),
            nullable=True,
        )

    if _has_column("dataset_versions", "source_dataset_version"):
        op.drop_column("dataset_versions", "source_dataset_version")

    if _has_column("audit_logs", "metadata") and not _has_column("audit_logs", "event_metadata"):
        op.alter_column(
            "audit_logs",
            "metadata",
            new_column_name="event_metadata",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=True,
        )

    if _has_column("market_bars", "session") and not _has_column("market_bars", "market_session"):
        op.alter_column(
            "market_bars",
            "quality_flags",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        )
        op.alter_column(
            "market_bars",
            "session",
            new_column_name="market_session",
            existing_type=postgresql.ENUM(
                "PREMARKET",
                "REGULAR",
                "POSTMARKET",
                "OVERNIGHT",
                name="market_session_enum",
            ),
            existing_nullable=False,
        )

    if _has_column("fill_quality_metrics", "policy_metadata") and not _has_column(
        "fill_quality_metrics", "policy_metadata_json"
    ):
        op.alter_column(
            "fill_quality_metrics",
            "policy_metadata",
            new_column_name="policy_metadata_json",
            existing_type=postgresql.JSONB(astext_type=sa.Text()),
            existing_nullable=True,
        )


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return column_name in {column["name"] for column in insp.get_columns(table_name)}

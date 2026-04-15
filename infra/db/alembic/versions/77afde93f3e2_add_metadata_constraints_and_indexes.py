"""add metadata constraints and indexes

Revision ID: 77afde93f3e2
Revises: 4981b5a58d62
Create Date: 2026-04-14 16:30:51.202261

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "77afde93f3e2"
down_revision: str | Sequence[str] | None = "4981b5a58d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ingestion_runs",
        "dataset_version",
        existing_type=sa.Integer(),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="dataset_version::text",
    )

    op.create_foreign_key(
        "fk_ingestion_runs_dataset_version",
        "ingestion_runs",
        "dataset_versions",
        ["dataset_version"],
        ["dataset_version_id"],
    )

    op.create_foreign_key(
        "fk_checksums_dataset_version",
        "checksums",
        "dataset_versions",
        ["dataset_version"],
        ["dataset_version_id"],
    )

    op.create_foreign_key(
        "fk_missing_bar_incidents_dataset_version",
        "missing_bar_incidents",
        "dataset_versions",
        ["dataset_version"],
        ["dataset_version_id"],
    )

    op.create_foreign_key(
        "fk_missing_bar_incidents_ingestion_run_id",
        "missing_bar_incidents",
        "ingestion_runs",
        ["ingestion_run_id"],
        ["ingestion_run_id"],
    )

    op.create_foreign_key(
        "fk_symbol_date_coverages_dataset_version",
        "symbol_date_coverages",
        "dataset_versions",
        ["dataset_version"],
        ["dataset_version_id"],
    )

    op.create_unique_constraint(
        "uq_checksums_dataset_version_object_path",
        "checksums",
        ["dataset_version", "object_path"],
    )

    op.create_unique_constraint(
        "uq_symbol_date_coverages_dataset_version_symbol_date",
        "symbol_date_coverages",
        ["dataset_version", "symbol", "date"],
    )
    op.create_index(
        "ix_ingestion_runs_dataset_version",
        "ingestion_runs",
        ["dataset_version"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_runs_status",
        "ingestion_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_runs_started_at",
        "ingestion_runs",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_runs_status_started_at",
        "ingestion_runs",
        ["status", "started_at"],
        unique=False,
    )

    op.create_index(
        "ix_dataset_versions_created_at",
        "dataset_versions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_dataset_versions_validation_status",
        "dataset_versions",
        ["validation_status"],
        unique=False,
    )

    op.create_index(
        "ix_checksums_dataset_version",
        "checksums",
        ["dataset_version"],
        unique=False,
    )

    op.create_index(
        "ix_missing_bar_incidents_dataset_version",
        "missing_bar_incidents",
        ["dataset_version"],
        unique=False,
    )
    op.create_index(
        "ix_missing_bar_incidents_ingestion_run_id",
        "missing_bar_incidents",
        ["ingestion_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_missing_bar_incidents_resolved_flag",
        "missing_bar_incidents",
        ["resolved_flag"],
        unique=False,
    )
    op.create_index(
        "ix_missing_bar_incidents_dataset_symbol_timestamp",
        "missing_bar_incidents",
        ["dataset_version", "symbol", "bar_timestamp"],
        unique=False,
    )

    op.create_index(
        "ix_symbol_date_coverages_dataset_version",
        "symbol_date_coverages",
        ["dataset_version"],
        unique=False,
    )
    op.create_index(
        "ix_symbol_date_coverages_symbol",
        "symbol_date_coverages",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "ix_symbol_date_coverages_dataset_symbol_date",
        "symbol_date_coverages",
        ["dataset_version", "symbol", "date"],
        unique=False,
    )

    op.create_index(
        "ix_audit_logs_run_id",
        "audit_logs",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_event_type",
        "audit_logs",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_event_timestamp",
        "audit_logs",
        ["event_timestamp"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_audit_logs_event_timestamp", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_run_id", table_name="audit_logs")

    op.drop_index(
        "ix_symbol_date_coverages_dataset_symbol_date", table_name="symbol_date_coverages"
    )
    op.drop_index("ix_symbol_date_coverages_symbol", table_name="symbol_date_coverages")
    op.drop_index("ix_symbol_date_coverages_dataset_version", table_name="symbol_date_coverages")

    op.drop_index(
        "ix_missing_bar_incidents_dataset_symbol_timestamp", table_name="missing_bar_incidents"
    )
    op.drop_index("ix_missing_bar_incidents_resolved_flag", table_name="missing_bar_incidents")
    op.drop_index("ix_missing_bar_incidents_ingestion_run_id", table_name="missing_bar_incidents")
    op.drop_index("ix_missing_bar_incidents_dataset_version", table_name="missing_bar_incidents")

    op.drop_index("ix_checksums_dataset_version", table_name="checksums")

    op.drop_index("ix_dataset_versions_validation_status", table_name="dataset_versions")
    op.drop_index("ix_dataset_versions_created_at", table_name="dataset_versions")

    op.drop_index("ix_ingestion_runs_status_started_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_dataset_version", table_name="ingestion_runs")

    op.drop_constraint(
        "uq_symbol_date_coverages_dataset_version_symbol_date",
        "symbol_date_coverages",
        type_="unique",
    )
    op.drop_constraint(
        "uq_checksums_dataset_version_object_path",
        "checksums",
        type_="unique",
    )

    op.drop_constraint(
        "fk_symbol_date_coverages_dataset_version",
        "symbol_date_coverages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_missing_bar_incidents_ingestion_run_id",
        "missing_bar_incidents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_missing_bar_incidents_dataset_version",
        "missing_bar_incidents",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_checksums_dataset_version",
        "checksums",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_ingestion_runs_dataset_version",
        "ingestion_runs",
        type_="foreignkey",
    )

    op.alter_column(
        "ingestion_runs",
        "dataset_version",
        existing_type=sa.String(length=64),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="dataset_version::integer",
    )

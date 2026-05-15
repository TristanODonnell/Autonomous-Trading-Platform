"""add operational_alerts table

Revision ID: kk78ll90mm12
Revises: jj67kk89ll01
Create Date: 2026-05-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "kk78ll90mm12"
down_revision: str | Sequence[str] | None = "jj67kk89ll01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = bind.dialect.get_table_names(bind, schema=None)
    if "operational_alerts" in existing_tables:
        return

    op.create_table(
        "operational_alerts",
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("alert_name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("job_name", sa.String(length=128), nullable=True),
        sa.Column("strategy_id", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.String(length=512), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recommended_action", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("snoozed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_by", sa.String(length=128), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("alert_id", name=op.f("pk_operational_alerts")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_operational_alerts_fingerprint")),
    )
    op.create_index(op.f("ix_operational_alerts_status"), "operational_alerts", ["status"])
    op.create_index(op.f("ix_operational_alerts_severity"), "operational_alerts", ["severity"])
    op.create_index(op.f("ix_operational_alerts_category"), "operational_alerts", ["category"])
    op.create_index(
        op.f("ix_operational_alerts_environment"), "operational_alerts", ["environment"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_operational_alerts_environment"), table_name="operational_alerts")
    op.drop_index(op.f("ix_operational_alerts_category"), table_name="operational_alerts")
    op.drop_index(op.f("ix_operational_alerts_severity"), table_name="operational_alerts")
    op.drop_index(op.f("ix_operational_alerts_status"), table_name="operational_alerts")
    op.drop_table("operational_alerts")

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class OperationalAlert(Base):
    __tablename__ = "operational_alerts"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    alert_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    job_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str] = mapped_column(String(512), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recommended_action: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    acknowledged_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    snoozed_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    snoozed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        sa.Index("ix_operational_alerts_status", "status"),
        sa.Index("ix_operational_alerts_severity", "severity"),
        sa.Index("ix_operational_alerts_category", "category"),
        sa.Index("ix_operational_alerts_environment", "environment"),
    )

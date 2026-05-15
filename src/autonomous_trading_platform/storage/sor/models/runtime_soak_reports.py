from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, UTCDateTimeType, uuid_pk_default


class RuntimeSoakReportRow(Base):
    __tablename__ = "runtime_soak_reports"

    report_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True, default=uuid_pk_default)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTimeType(), nullable=False)
    window_end: Mapped[datetime] = mapped_column(UTCDateTimeType(), nullable=False)
    failed_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    runtime_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        sa.Index("ix_runtime_soak_reports_checked_at", "checked_at"),
        sa.Index("ix_runtime_soak_reports_status", "status"),
        sa.Index("ix_runtime_soak_reports_environment", "environment"),
    )

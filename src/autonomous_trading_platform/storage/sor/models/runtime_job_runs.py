from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class RuntimeJobRuns(Base):
    __tablename__ = "runtime_job_runs"

    job_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    job_name: Mapped[str] = mapped_column(String(128), nullable=False)

    parent_job_run_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("runtime_job_runs.job_run_id"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)

    started_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    completed_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(),
        nullable=True,
    )

    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    input_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

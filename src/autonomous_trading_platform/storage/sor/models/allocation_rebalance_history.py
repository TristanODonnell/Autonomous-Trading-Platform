from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class AllocationRebalanceHistory(Base):
    __tablename__ = "allocation_rebalance_history"

    __table_args__ = (
        Index("ix_arh_started_at", "started_at"),
        Index("ix_arh_status", "status"),
        Index("ix_arh_run_id", "run_id"),
    )

    rebalance_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # status: running | completed | noop | skipped_interval | skipped_concurrent
    #         | skipped_disabled | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    skipped_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    started_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    completed_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    strategies_evaluated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocation_changes_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_allocation_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    churn_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

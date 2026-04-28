from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class MetricsSummary(Base):
    __tablename__ = "metrics_summary"

    metrics_snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("simulation_runs.run_id"),
        nullable=False,
    )

    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winning_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    losing_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)

    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

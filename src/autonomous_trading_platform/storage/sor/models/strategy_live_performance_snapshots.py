from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class StrategyLivePerformanceSnapshot(Base):
    __tablename__ = "strategy_live_performance_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)

    realized_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    rolling_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    live_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winning_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_live: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_since_profitable_day: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

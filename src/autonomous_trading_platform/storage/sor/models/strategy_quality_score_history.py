from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class StrategyQualityScoreHistory(Base):
    __tablename__ = "strategy_quality_score_history"

    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rebalance_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    live_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    backtest_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    blended_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    score_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    data_window: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    governance_state: Mapped[str | None] = mapped_column(String(64), nullable=True)

    computed_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_sqsh_strategy_id_computed_at", "strategy_id", "computed_at"),
        Index("ix_sqsh_rebalance_run_id", "rebalance_run_id"),
    )

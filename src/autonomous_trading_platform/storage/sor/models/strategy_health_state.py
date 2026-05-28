from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class StrategyHealthStateRow(Base):
    __tablename__ = "strategy_health_states"

    health_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    health_status: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_health_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    health_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    consecutive_decline_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score_trend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latest_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_health_evaluated_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    last_transition_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    evaluation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_rebalance_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_strategy_health_states_strategy_id"),
        Index("ix_shs_strategy_id", "strategy_id"),
        Index("ix_shs_health_status", "health_status"),
    )

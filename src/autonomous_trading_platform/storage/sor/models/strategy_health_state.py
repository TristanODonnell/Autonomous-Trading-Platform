from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class StrategyHealthStateRow(Base):
    __tablename__ = "strategy_health_states"

    health_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # -----------------------------------------------------------------------
    # Core health status (FINDING-09 fields — unchanged)
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # Lifecycle fields (Rec 6.3 — anti-flapping, suspension, allocation penalty)
    # -----------------------------------------------------------------------
    # Suspension state
    suspended_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    suspension_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    operator_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Anti-flapping: no recovery transitions allowed while cooldown_expires_at > now
    cooldown_expires_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )

    # Escalation counters: used to trigger auto-suspension and recovery guards
    consecutive_critical_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # Allocation impact
    allocation_penalty: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Which lifecycle mode last evaluated this strategy
    lifecycle_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_strategy_health_states_strategy_id"),
        Index("ix_shs_strategy_id", "strategy_id"),
        Index("ix_shs_health_status", "health_status"),
        Index("ix_shs_operator_review_required", "operator_review_required"),
    )

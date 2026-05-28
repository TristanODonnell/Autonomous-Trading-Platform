from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class StrategyHealthTransitionRow(Base):
    """
    Append-only audit trail of every strategy health lifecycle state transition.

    Never updated after insert — provides a complete, tamper-evident history of
    how a strategy moved through the health lifecycle and why.
    """

    __tablename__ = "strategy_health_transitions"

    transition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)

    transition_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # "system" or "operator:<actor>" for manual interventions
    triggered_by: Mapped[str] = mapped_column(String(256), nullable=False, default="system")

    # Snapshot of the metrics that triggered this transition
    triggering_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    drawdown_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rolling_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    allocation_penalty_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    cooldown_expires_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )

    # Link back to the rebalance/evaluation run that triggered this
    evaluation_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_sht_strategy_id", "strategy_id"),
        Index("ix_sht_created_at", "created_at"),
        Index("ix_sht_to_status", "to_status"),
        Index("ix_sht_strategy_created", "strategy_id", "created_at"),
    )

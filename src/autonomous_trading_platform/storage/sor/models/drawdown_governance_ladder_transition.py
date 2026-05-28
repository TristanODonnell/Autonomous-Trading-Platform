from __future__ import annotations

from typing import Any

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class DrawdownGovernanceLadderTransitionRow(Base):
    """
    Append-only audit trail of every drawdown governance ladder state change.

    Never updated after insert — provides a tamper-evident record of when and
    why a strategy moved between ladder rungs, including the drawdown metrics
    and allocation scalar at the time of the transition.
    """

    __tablename__ = "drawdown_governance_ladder_transitions"

    transition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)

    transition_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # "system" or "operator:<actor>" for manual interventions
    triggered_by: Mapped[str] = mapped_column(String(256), nullable=False, default="system")

    # Drawdown metrics at transition time
    realized_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_allowed: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)

    allocation_scalar_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    cooldown_expires_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )

    # Snapshot of context metrics
    triggering_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Link back to the evaluation run that triggered this
    evaluation_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        Index("ix_ddg_trans_strategy_id", "strategy_id"),
        Index("ix_ddg_trans_created_at", "created_at"),
        Index("ix_ddg_trans_to_state", "to_state"),
        Index("ix_ddg_trans_strategy_created", "strategy_id", "created_at"),
    )

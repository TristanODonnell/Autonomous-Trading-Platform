from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.base import Base
from autonomous_trading_platform.storage.sor.models.helpers.sa_types import UTCDateTimeType


class DrawdownGovernanceLadderStateRow(Base):
    """
    Per-strategy current drawdown governance ladder state.

    One row per strategy — upserted on each evaluation cycle.  Never deleted;
    the append-only transition table carries the full history.
    """

    __tablename__ = "drawdown_governance_ladder_states"

    ladder_state_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # -----------------------------------------------------------------------
    # Current ladder position
    # -----------------------------------------------------------------------
    ladder_state: Mapped[str] = mapped_column(String(32), nullable=False, default="normal")
    previous_ladder_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transition_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # -----------------------------------------------------------------------
    # Drawdown metrics at last evaluation
    # -----------------------------------------------------------------------
    realized_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_allowed: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)

    # -----------------------------------------------------------------------
    # Allocation scalar applied at the current rung
    # -----------------------------------------------------------------------
    allocation_scalar: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0")
    )

    # -----------------------------------------------------------------------
    # Anti-flapping
    # -----------------------------------------------------------------------
    cooldown_expires_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    evaluation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # -----------------------------------------------------------------------
    # Governance / operator state
    # -----------------------------------------------------------------------
    # Breach recovery requires explicit operator acknowledgement when
    # breach_requires_operator_ack is enabled in config.
    operator_ack_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    operator_acknowledged_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    operator_acknowledged_by: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # -----------------------------------------------------------------------
    # Timestamps
    # -----------------------------------------------------------------------
    last_transition_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    last_evaluated_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    created_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    updated_at: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)

    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_ddg_ladder_state_strategy_id"),
        Index("ix_ddg_ladder_state_strategy_id", "strategy_id"),
        Index("ix_ddg_ladder_state_ladder_state", "ladder_state"),
        Index("ix_ddg_ladder_state_operator_ack_required", "operator_ack_required"),
    )

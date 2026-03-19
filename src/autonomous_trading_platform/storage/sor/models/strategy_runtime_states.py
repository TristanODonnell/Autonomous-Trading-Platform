from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyState,
)

from .base import Base
from .helpers.sa_types import UTCDateTimeType


class StrategyRuntimeState(Base):
    __tablename__ = "strategy_runtime_states"

    strategy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    current_state: Mapped[StrategyState] = mapped_column(
        SAEnum(StrategyState, name="strategy_runtime_state_enum"),
        nullable=False,
    )

    last_signal_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(),
        nullable=True,
    )
    last_transition_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    cooldown_until: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(),
        nullable=True,
    )
    updated_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

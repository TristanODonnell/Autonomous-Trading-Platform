# autonomous_trading_platform/storage/sor/models/signals.py

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.common.types import Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, QuantityType, UTCDateTimeType


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[SignalDirection] = mapped_column(
        SAEnum(SignalDirection, name="signal_direction_enum"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_position: Mapped[Quantity | None] = mapped_column(QuantityType(), nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "bar_timestamp",
            "symbol",
            "strategy_id",
            name="uq_signals_run_bar_symbol_strategy",
        ),
    )

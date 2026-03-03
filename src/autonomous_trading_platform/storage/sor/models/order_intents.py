# autonomous_trading_platform/storage/sor/models/order_intents.py

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class OrderIntents(Base):
    __tablename__ = "order_intents"

    intent_id: Mapped[UUID] = mapped_column(UUID_PK, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    bar_timestamp: Mapped[UTCDateTime] = mapped_column(UTCDateTimeType(), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[Side] = mapped_column(SAEnum(Side, name="side_enum"), nullable=False)
    qty: Mapped[Quantity | None] = mapped_column(QuantityType(), nullable=True)
    notional: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    order_type: Mapped[OrderType] = mapped_column(
        SAEnum(OrderType, name="order_type_enum"), nullable=False
    )
    limit_price: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    stop_price: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    time_in_force: Mapped[TimeInForce] = mapped_column(
        SAEnum(TimeInForce, name="time_in_force_enum"), nullable=False
    )
    extended_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    client_order_id: Mapped[str] = mapped_column(String, nullable=False)
    metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_order_intents_run_id_idempotency_key",
        ),
    )

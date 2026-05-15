# autonomous_trading_platform/storage/sor/models/broker_orders.py

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime

from .base import Base
from .helpers.sa_types import UUID_PK, MoneyType, QuantityType, UTCDateTimeType


class BrokerOrder(Base):
    __tablename__ = "broker_orders"

    broker_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    run_id: Mapped[UUID] = mapped_column(UUID_PK, nullable=False)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[Side] = mapped_column(SAEnum(Side, name="side_enum"), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        SAEnum(OrderType, name="order_type_enum"), nullable=False
    )
    time_in_force: Mapped[TimeInForce] = mapped_column(
        SAEnum(TimeInForce, name="time_in_force_enum"), nullable=False
    )
    extended_hours: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qty: Mapped[Quantity | None] = mapped_column(QuantityType(), nullable=True)
    notional: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    limit_price: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    stop_price: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status_enum"), nullable=False
    )
    submitted_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)
    updated_at: Mapped[UTCDateTime] = mapped_column(
        UTCDateTimeType(), nullable=False, default=lambda: datetime.now(UTC)
    )
    filled_qty: Mapped[Quantity] = mapped_column(
        QuantityType(), nullable=False, default=Decimal("0")
    )
    avg_fill_price: Mapped[Money | None] = mapped_column(MoneyType(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_broker_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    requested_qty: Mapped[Quantity | None] = mapped_column(QuantityType(), nullable=True)
    signal_generated_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    submitted_to_broker_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    broker_acknowledged_at: Mapped[UTCDateTime | None] = mapped_column(
        UTCDateTimeType(), nullable=True
    )
    first_fill_at: Mapped[UTCDateTime | None] = mapped_column(UTCDateTimeType(), nullable=True)

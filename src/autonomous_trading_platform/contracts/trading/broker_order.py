# contracts/trading/broker_order.py

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime


class BrokerOrder(BaseModel):
    broker_order_id: str
    client_order_id: str
    intent_id: UUID
    run_id: UUID
    broker: Literal["alpaca"]
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType
    time_in_force: TimeInForce
    extended_hours: bool
    qty: Quantity | None = None
    notional: Money | None = None
    limit_price: Money | None = None
    stop_price: Money | None = None
    status: OrderStatus
    submitted_at: UTCDateTime | None = None
    updated_at: UTCDateTime
    filled_qty: Quantity = Decimal("0")
    avg_fill_price: Money | None = None
    last_error: str | None = None
    raw_broker_payload: dict[str, Any] | None = None
    requested_qty: Quantity | None = None
    signal_generated_at: UTCDateTime | None = None
    submitted_to_broker_at: UTCDateTime | None = None
    broker_acknowledged_at: UTCDateTime | None = None
    first_fill_at: UTCDateTime | None = None

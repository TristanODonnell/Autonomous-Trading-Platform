# contracts/trading/broker_order.py
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)


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
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus
    submitted_at: datetime | None = None
    updated_at: datetime
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    last_error: str | None = None
    raw_broker_payload: dict[str, Any] | None = None
    requested_qty: float | None = None

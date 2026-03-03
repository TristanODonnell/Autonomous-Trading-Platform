# autonomous_trading_platform/contracts/trading/order_intent.py
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime


class OrderIntent(BaseModel):
    intent_id: UUID
    idempotency_key: str
    run_id: UUID
    strategy_id: str
    timestamp: UTCDateTime
    bar_timestamp: UTCDateTime
    symbol: str
    side: Side
    qty: Quantity | None = None
    notional: Money | None = None
    order_type: OrderType
    limit_price: Money | None = None
    stop_price: Money | None = None
    time_in_force: TimeInForce
    extended_hours: bool
    client_order_id: str
    metadata: dict[str, Any] | None = None

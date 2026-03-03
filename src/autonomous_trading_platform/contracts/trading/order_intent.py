# autonomous_trading_platform/contracts/trading/order_intent.py
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    OrderType,
    Side,
    TimeInForce,
)


class OrderIntent(BaseModel):
    intent_id: UUID
    idempotency_key: str
    run_id: UUID
    strategy_id: str
    timestamp: datetime
    bar_timestamp: datetime
    symbol: str
    side: Side
    qty: float | None = None
    notional: float | None = None
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce
    extended_hours: bool
    client_order_id: str
    metadata: dict[str, Any] | None = None

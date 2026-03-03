# autonomous_trading_platform/contracts/trading/fill.py

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    Side,
)


class Fill(BaseModel):
    fill_id: str
    broker_order_id: str
    intent_id: UUID
    run_id: UUID
    timestamp: datetime
    symbol: str
    side: Side
    quantity: float
    price: float
    fees: float | None = None
    liquidity: LiquiditySide | None = None
    venue: str | None = None
    metadata: dict[str, Any] | None = None

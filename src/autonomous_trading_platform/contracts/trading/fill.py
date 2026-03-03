# autonomous_trading_platform/contracts/trading/fill.py
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import (
    LiquiditySide,
    Side,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime


class Fill(BaseModel):
    fill_id: str
    broker_order_id: str
    intent_id: UUID
    run_id: UUID
    timestamp: UTCDateTime
    symbol: str
    side: Side
    quantity: Quantity
    price: Money
    fees: Money | None = None
    liquidity: LiquiditySide | None = None
    venue: str | None = None
    metadata: dict[str, Any] | None = None

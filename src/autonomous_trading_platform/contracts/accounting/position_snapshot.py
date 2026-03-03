# autonomous_trading_platform/contracts/accounting/position_snapshot.py

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.common.types import Money, Quantity, UTCDateTime


class Position(BaseModel):
    symbol: str
    quantity: Quantity
    avg_cost: Money | None = None
    market_price: Money | None = None
    market_value: Money | None = None
    unrealized_pnl: Money | None = None


class PositionSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: UTCDateTime
    positions: list[Position]
    source: OrderSource

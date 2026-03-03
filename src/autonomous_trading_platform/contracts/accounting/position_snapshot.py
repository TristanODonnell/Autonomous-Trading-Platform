# autonomous_trading_platform/contracts/accounting/position_snapshot.py
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import OrderSource


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None


class PositionSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: datetime
    positions: list[Position]
    source: OrderSource

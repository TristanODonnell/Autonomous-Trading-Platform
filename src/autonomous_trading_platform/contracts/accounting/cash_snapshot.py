# autonomous_trading_platform/contracts/accounting/cash_snapshot.py

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import OrderSource


class CashSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: datetime
    currency: str
    cash: float
    buying_power: float
    reserved_cash: float
    equity: float | None = None
    source: OrderSource
    capital_bucket: float | None = None

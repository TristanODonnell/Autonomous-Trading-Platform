# autonomous_trading_platform/contracts/accounting/cash_snapshot.py

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import OrderSource
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime


class CashSnapshot(BaseModel):
    snapshot_id: UUID
    run_id: UUID
    timestamp: UTCDateTime
    currency: str
    cash: Money
    buying_power: Money
    reserved_cash: Money
    equity: Money | None = None
    source: OrderSource
    capital_bucket: Money | None = None
    # Settlement-aware fields (F-06). None → legacy: treat all cash as settled.
    settled_cash: Money | None = None
    unsettled_cash: Money | None = None

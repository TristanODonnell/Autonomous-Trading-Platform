# autonomous_trading_platform/contracts/market/corporate_action.py
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.common.types import Money, UTCDateTime


class CorporateAction(BaseModel):
    action_id: str
    symbol: str
    action_type: CorporateActionType

    effective_date: date
    announced_date: date | None = None
    record_date: date | None = None
    payable_date: date | None = None

    split_ratio: float | None = None
    cash_amount: Money | None = None
    currency: str

    new_symbol: str

    source: str
    ingested_at: UTCDateTime
    metadata: dict[str, Any] | None = None

# autonomous_trading_platform/contracts/market/corporate_action.py

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import CorporateActionType


class CorporateAction(BaseModel):
    action_id: str
    symbol: str
    type: CorporateActionType
    effective_date: date
    announced_date: date | None = None
    record_date: date | None = None
    payable_date: date | None = None
    ratio_or_amount: float
    new_symbol: str
    currency: str
    source: str
    ingested_at: datetime
    metadata: dict[str, Any] | None = None

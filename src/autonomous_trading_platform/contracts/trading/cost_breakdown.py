from __future__ import annotations

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import Money


class CostBreakdown(BaseModel):
    commission_cost: Money
    spread_cost: Money
    slippage_cost: Money
    total_cost: Money

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from autonomous_trading_platform.contracts.common.types import Money


class CostModelConfig(BaseModel):
    fixed_commission: Money = Decimal("0")
    per_share_commission: Money = Decimal("0")
    default_half_spread: Money = Decimal("0")
    extra_slippage_bps: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))

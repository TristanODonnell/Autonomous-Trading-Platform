from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity


class SlippageMeasurement(BaseModel):
    side: Side

    reference_price: Money  # mid price at submission
    fill_price: Money

    quantity: Quantity

    slippage_per_share: Money
    slippage_notional: Money
    slippage_bps: Decimal

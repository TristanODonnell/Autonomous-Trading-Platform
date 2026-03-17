from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.trading.slippage_measurement import (
    SlippageMeasurement,
)

BPS_DENOMINATOR = Decimal("10000")


class RealisedSlippageService:
    def calculate(
        self,
        side: Side,
        reference_price: Money,
        fill_price: Money,
        quantity: Quantity,
    ) -> SlippageMeasurement:
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        if side == Side.BUY:
            slippage_per_share = fill_price - reference_price
        elif side == Side.SELL:
            slippage_per_share = reference_price - fill_price
        else:
            raise ValueError(f"unsupported side: {side}")

        slippage_notional = slippage_per_share * quantity
        slippage_bps = (slippage_per_share / reference_price) * BPS_DENOMINATOR
        return SlippageMeasurement(
            side=side,
            reference_price=reference_price,
            fill_price=fill_price,
            quantity=quantity,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_bps=slippage_bps,
        )

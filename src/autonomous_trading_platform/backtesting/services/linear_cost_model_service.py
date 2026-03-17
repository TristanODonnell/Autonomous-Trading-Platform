from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.backtesting.models.cost_model_config import (
    CostModelConfig,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity
from autonomous_trading_platform.contracts.trading.cost_breakdown import CostBreakdown

BPS_DENOMINATOR = Decimal("10000")


class LinearCostModelService:
    def __init__(self, config: CostModelConfig) -> None:
        self.config = config

    def estimate_costs(
        self,
        quantity: Quantity,
        reference_price: Money,
        half_spread: Money | None = None,
    ) -> CostBreakdown:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")

        effective_half_spread = (
            half_spread if half_spread is not None else self.config.default_half_spread
        )

        if effective_half_spread < 0:
            raise ValueError("half_spread cannot be negative")

        commission_cost = self.config.fixed_commission + self.config.per_share_commission * quantity

        spread_cost = effective_half_spread * quantity

        slippage_cost = (
            (self.config.extra_slippage_bps / BPS_DENOMINATOR) * reference_price * quantity
        )
        total_cost = commission_cost + spread_cost + slippage_cost

        return CostBreakdown(
            commission_cost=commission_cost,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            total_cost=total_cost,
        )

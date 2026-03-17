from __future__ import annotations

from decimal import Decimal

from autonomous_trading_platform.backtesting.models.slippage_model_config import (
    SlippageModelConfig,
)
from autonomous_trading_platform.contracts.common.types import Money, Quantity

BPS_DENOMINATOR = Decimal("10000")


class VolumeShareSlippageModelService:
    def __init__(self, config: SlippageModelConfig) -> None:
        self.config = config

    def estimate_slippage_cost(
        self,
        quantity: Quantity,
        reference_price: Money,
        bar_volume: Quantity,
    ) -> Money:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        if bar_volume <= 0:
            raise ValueError("bar_volume must be positive")

        raw_volume_share = quantity / bar_volume
        capped_volume_share = min(raw_volume_share, self.config.max_volume_share)

        slippage_bps = self.config.impact_coefficient_bps * capped_volume_share

        slippage_cost = (slippage_bps / BPS_DENOMINATOR) * reference_price * quantity

        return slippage_cost

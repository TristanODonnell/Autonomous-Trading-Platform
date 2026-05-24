from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext


@dataclass(slots=True)
class SimulationCostModelConfig:
    commission_per_share: Decimal = Decimal("0.0000")
    min_commission: Decimal = Decimal("0.00")


@dataclass(slots=True)
class SimulatedTradeCosts:
    reference_price: Decimal
    fill_price: Decimal
    slippage_per_share: Decimal
    slippage_notional: Decimal
    slippage_rate: Decimal  # effective rate: slippage_per_share / reference_price
    commission: Decimal
    total_cost: Decimal


class SimulationCostModelService:
    """Applies slippage and commission to a simulated fill.

    Accepts any slippage model implementing calculate_fill_price(side, market_price, context)
    and config_summary() — fixed-rate, volume-share, or spread-aware.
    """

    def __init__(
        self,
        config: SimulationCostModelConfig,
        slippage_model: Any,
    ):
        self.config = config
        self.slippage_model = slippage_model

    def apply_costs(
        self,
        *,
        side: Side,
        reference_price: Decimal,
        quantity: Decimal,
        context: SlippageContext | None = None,
    ) -> SimulatedTradeCosts:
        fill_price = self.slippage_model.calculate_fill_price(
            side=side,
            market_price=reference_price,
            context=context,
        )

        if side == Side.BUY:
            slippage_per_share = fill_price - reference_price
        elif side == Side.SELL:
            slippage_per_share = reference_price - fill_price
        else:
            raise ValueError(f"unsupported side={side}")

        slippage_notional = slippage_per_share * quantity

        commission = max(
            self.config.min_commission,
            self.config.commission_per_share * quantity,
        )

        effective_slippage_rate = (
            slippage_per_share / reference_price if reference_price > 0 else Decimal("0")
        )

        return SimulatedTradeCosts(
            reference_price=reference_price,
            fill_price=fill_price,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_rate=effective_slippage_rate,
            commission=commission,
            total_cost=slippage_notional + commission,
        )

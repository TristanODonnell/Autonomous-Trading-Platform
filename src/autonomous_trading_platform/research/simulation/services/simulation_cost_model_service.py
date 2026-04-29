from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side


@dataclass(slots=True)
class SimulationCostModelConfig:
    commission_per_share: Decimal = Decimal("0.0000")
    min_commission: Decimal = Decimal("0.00")
    slippage_bps: Decimal = Decimal("1.0")


@dataclass(slots=True)
class SimulatedTradeCosts:
    reference_price: Decimal
    fill_price: Decimal
    slippage_per_share: Decimal
    slippage_notional: Decimal
    slippage_bps: Decimal
    commission: Decimal
    total_cost: Decimal


class SimulationCostModelService:
    def __init__(self, config: SimulationCostModelConfig):
        self.config = config

    def apply_costs(
        self,
        *,
        side: Side,
        reference_price: Decimal,
        quantity: Decimal,
    ) -> SimulatedTradeCosts:
        bps_multiplier = self.config.slippage_bps / Decimal("10000")

        if side == Side.BUY:
            fill_price = reference_price * (Decimal("1") + bps_multiplier)
            slippage_per_share = fill_price - reference_price
        elif side == Side.SELL:
            fill_price = reference_price * (Decimal("1") - bps_multiplier)
            slippage_per_share = reference_price - fill_price
        else:
            raise ValueError(f"unsupported side={side}")

        slippage_notional = slippage_per_share * quantity

        commission = max(
            self.config.min_commission,
            self.config.commission_per_share * quantity,
        )

        return SimulatedTradeCosts(
            reference_price=reference_price,
            fill_price=fill_price,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_bps=self.config.slippage_bps,
            commission=commission,
            total_cost=slippage_notional + commission,
        )

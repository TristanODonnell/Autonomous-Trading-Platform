from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.slippage_model import SlippageModel


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
    slippage_rate: Decimal
    commission: Decimal
    total_cost: Decimal


class SimulationCostModelService:
    def __init__(
        self,
        config: SimulationCostModelConfig,
        slippage_model: SlippageModel,
    ):
        self.config = config
        self.slippage_model = slippage_model

    def apply_costs(
        self,
        *,
        side: Side,
        reference_price: Decimal,
        quantity: Decimal,
    ) -> SimulatedTradeCosts:
        fill_price = self.slippage_model.calculate_fill_price(
            side=side,
            market_price=reference_price,
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

        return SimulatedTradeCosts(
            reference_price=reference_price,
            fill_price=fill_price,
            slippage_per_share=slippage_per_share,
            slippage_notional=slippage_notional,
            slippage_rate=self.slippage_model.config.slippage_rate,
            commission=commission,
            total_cost=slippage_notional + commission,
        )

from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.cost_model_type import CostModelType
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext


@dataclass(slots=True)
class SlippageModelConfig:
    slippage_rate: Decimal = Decimal("0.0001")


class SlippageModel:
    """Fixed-rate slippage model. Retained for testing and simple/deterministic mode.

    Ignores bar context; applies a constant rate regardless of order size or liquidity.
    Do not use as the realistic simulation default.
    """

    model_type = CostModelType.FIXED_BPS

    def __init__(self, config: SlippageModelConfig):
        self.config = config

    def calculate_fill_price(
        self,
        *,
        side: Side,
        market_price: Decimal,
        context: SlippageContext | None = None,  # noqa: ARG002
    ) -> Decimal:
        if side == Side.BUY:
            return market_price * (Decimal("1") + self.config.slippage_rate)
        if side == Side.SELL:
            return market_price * (Decimal("1") - self.config.slippage_rate)
        raise ValueError(f"unsupported side={side}")

    def config_summary(self) -> dict[str, object]:
        return {
            "model_type": self.model_type.value,
            "slippage_rate": str(self.config.slippage_rate),
        }

from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side


@dataclass(slots=True)
class SlippageModelConfig:
    slippage_rate: Decimal = Decimal("0.0001")


class SlippageModel:
    def __init__(self, config: SlippageModelConfig):
        self.config = config

    def calculate_fill_price(
        self,
        *,
        side: Side,
        market_price: Decimal,
    ) -> Decimal:
        if side == Side.BUY:
            return market_price * (Decimal("1") + self.config.slippage_rate)

        if side == Side.SELL:
            return market_price * (Decimal("1") - self.config.slippage_rate)

        raise ValueError(f"unsupported side={side}")

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from autonomous_trading_platform.contracts.common.enums import Side
from autonomous_trading_platform.research.simulation.models.cost_model_type import CostModelType
from autonomous_trading_platform.research.simulation.models.slippage_context import SlippageContext

_BPS = Decimal("10000")
_HALF = Decimal("2")


@dataclass(slots=True)
class SpreadAwareSlippageConfig:
    # half_spread_bps = (high - low) / close / 2 * 10_000, clamped to [min, max]
    min_half_spread_bps: Decimal = Decimal("5")
    max_half_spread_bps: Decimal = Decimal("100")
    spread_multiplier: Decimal = Decimal("1")


class SpreadAwareSlippageModel:
    """Spread-based slippage model using OHLC range as a half-spread proxy.

    Suitable when volume data is unreliable. Falls back to min_half_spread_bps
    when bar OHLC context is unavailable.
    """

    model_type = CostModelType.SPREAD_AWARE

    def __init__(self, config: SpreadAwareSlippageConfig | None = None) -> None:
        self.config = config if config is not None else SpreadAwareSlippageConfig()

    def calculate_fill_price(
        self,
        *,
        side: Side,
        market_price: Decimal,
        context: SlippageContext | None = None,
    ) -> Decimal:
        slippage_rate = self._compute_slippage_rate(context)

        if side == Side.BUY:
            return market_price * (Decimal("1") + slippage_rate)
        if side == Side.SELL:
            return market_price * (Decimal("1") - slippage_rate)
        raise ValueError(f"unsupported side={side}")

    def _compute_slippage_rate(self, context: SlippageContext | None) -> Decimal:
        if (
            context is not None
            and context.bar_high is not None
            and context.bar_low is not None
            and context.bar_close is not None
            and context.bar_close > 0
        ):
            raw_bps = (context.bar_high - context.bar_low) / context.bar_close / _HALF * _BPS
            clamped_bps = max(
                self.config.min_half_spread_bps,
                min(self.config.max_half_spread_bps, raw_bps),
            )
            return clamped_bps * self.config.spread_multiplier / _BPS

        return self.config.min_half_spread_bps / _BPS

    def config_summary(self) -> dict[str, object]:
        return {
            "model_type": self.model_type.value,
            "min_half_spread_bps": str(self.config.min_half_spread_bps),
            "max_half_spread_bps": str(self.config.max_half_spread_bps),
            "spread_multiplier": str(self.config.spread_multiplier),
        }

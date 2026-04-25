from __future__ import annotations

from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
)
from autonomous_trading_platform.strategy.indicators.momentum import momentum
from autonomous_trading_platform.strategy.signal_logic.threshold_rule import ThresholdRule


class MomentumStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str,
        lookback: int = 5,
        buy_above: float = 0.0,
        sell_below: float = 0.0,
    ) -> None:
        self._strategy_id = strategy_id
        self.lookback = lookback
        self.buy_above = buy_above
        self.sell_below = sell_below

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        closes = extract_closes(context.bars)
        value = momentum(closes, self.lookback)

        if value is None:
            return None

        result = ThresholdRule(
            value=value,
            buy_above=self.buy_above,
            sell_below=self.sell_below,
        ).evaluate()

        if result.direction is None:
            return None

        params = {
            "strategy_type": "momentum",
            "lookback": self.lookback,
            **result.params,
        }

        return Signal(
            signal_id=build_signal_id(
                run_id=context.run_id,
                strategy_id=context.strategy_id,
                symbol=context.symbol,
                bar_timestamp=context.bar_timestamp,
                direction=result.direction,
                params=params,
            ),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=result.direction,
            confidence=result.confidence,
            params=params,
        )

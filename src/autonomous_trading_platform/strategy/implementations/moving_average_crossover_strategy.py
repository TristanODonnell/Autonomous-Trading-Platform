from __future__ import annotations

from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
)
from autonomous_trading_platform.strategy.indicators.trend import simple_moving_average
from autonomous_trading_platform.strategy.signal_logic.crossover_rule import CrossoverRule


class MovingAverageCrossoverStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str,
        short_window: int = 10,
        long_window: int = 30,
    ) -> None:
        if short_window <= 0 or long_window <= 0:
            raise ValueError("windows must be positive")
        if short_window >= long_window:
            raise ValueError("short_window must be less than long_window")

        self._strategy_id = strategy_id
        self.short_window = short_window
        self.long_window = long_window

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        closes = extract_closes(context.bars)

        if len(closes) < self.long_window + 1:
            return None

        previous_fast = simple_moving_average(closes[:-1], self.short_window)
        previous_slow = simple_moving_average(closes[:-1], self.long_window)
        current_fast = simple_moving_average(closes, self.short_window)
        current_slow = simple_moving_average(closes, self.long_window)

        if (
            previous_fast is None
            or previous_slow is None
            or current_fast is None
            or current_slow is None
        ):
            return None

        result = CrossoverRule(
            previous_fast=previous_fast,
            previous_slow=previous_slow,
            current_fast=current_fast,
            current_slow=current_slow,
        ).evaluate()

        if result.direction is None:
            return None

        params = {
            "strategy_type": "moving_average_crossover",
            "short_window": self.short_window,
            "long_window": self.long_window,
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

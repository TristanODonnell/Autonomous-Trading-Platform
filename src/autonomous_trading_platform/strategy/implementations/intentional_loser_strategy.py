from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
)


class IntentionalLoserStrategy(BaseStrategy):
    """
    Debug strategy designed to lose money in a long-only simulator.

    Rules:
    - If price moved up, BUY after the move.
    - If price moved down, SELL after the move.
    - This tends to buy high and sell low.
    """

    def __init__(
        self,
        strategy_id: str = "intentional_loser_v1",
        price_change_threshold: float = 0.0,
    ) -> None:
        if price_change_threshold < 0:
            raise ValueError("price_change_threshold must be non-negative")

        self._strategy_id = strategy_id
        self.price_change_threshold = price_change_threshold

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        closes = extract_closes(context.bars)

        if len(closes) < 2:
            return None

        previous_close = closes[-2]
        current_close = closes[-1]
        price_delta = current_close - previous_close

        if price_delta > self.price_change_threshold:
            direction = SignalDirection.SELL  # <-- flipped
            reason = "sell_after_price_increase"
        elif price_delta < -self.price_change_threshold:
            direction = SignalDirection.BUY  # <-- flipped
            reason = "buy_after_price_decrease"
        else:
            return None

        params = {
            "strategy_type": "intentional_loser",
            "previous_close": previous_close,
            "current_close": current_close,
            "price_delta": price_delta,
            "price_change_threshold": self.price_change_threshold,
            "reason": reason,
        }

        return Signal(
            signal_id=build_signal_id(
                run_id=context.run_id,
                strategy_id=context.strategy_id,
                symbol=context.symbol,
                bar_timestamp=context.bar_timestamp,
                direction=direction,
                params=params,
            ),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=direction,
            confidence=1.0,
            params=params,
        )

from __future__ import annotations

from typing import Any
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy


class StubStrategy(BaseStrategy):
    """
    Very simple placeholder strategy used to validate the strategy architecture.

    Rules:
    - If the most recent close is greater than the previous close, emit BUY.
    - If the most recent close is less than the previous close, emit SELL.
    - Otherwise emit no signal.

    This strategy is intentionally simplistic. Its purpose is to validate:
    - pluggable strategy loading
    - deterministic evaluation
    - bar-close-only signal generation
    """

    def __init__(self, strategy_id: str = "stub_strategy_v1") -> None:
        self._strategy_id = strategy_id

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        bars = context.bars

        if len(bars) < 2:
            return None

        previous_bar = bars[-2]
        current_bar = bars[-1]

        previous_close = self._get_close(previous_bar)
        current_close = self._get_close(current_bar)

        if current_close > previous_close:
            direction = SignalDirection.BUY
            confidence = 0.55
        elif current_close < previous_close:
            direction = SignalDirection.SELL
            confidence = 0.55
        else:
            return None

        return Signal(
            signal_id=uuid4(),
            run_id=context.run_id,
            timestamp=context.evaluation_timestamp,
            bar_timestamp=context.bar_timestamp,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            direction=direction,
            confidence=confidence,
            params={
                "strategy_type": "stub",
                "previous_close": previous_close,
                "current_close": current_close,
            },
        )

    @staticmethod
    def _get_close(bar: Any) -> float:
        """
        Extract close price from a bar-like object.

        Supports either:
        - bar.close attribute
        - dict-style bar["close"]

        This keeps the stub flexible while the surrounding architecture settles.
        """
        if hasattr(bar, "close"):
            return float(bar.close)

        if isinstance(bar, dict) and "close" in bar:
            return float(bar["close"])

        raise ValueError("Bar object does not expose a close price.")

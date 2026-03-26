from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy


class StubStrategy(BaseStrategy):
    """
    Very simple placeholder strategy used to validate the strategy architecture.

    Rules:
    - If the most recent close is greater than the previous close by more than
      the configured threshold, emit BUY.
    - If the most recent close is less than the previous close by more than
      the configured threshold, emit SELL.
    - Otherwise emit no signal.
    """

    def __init__(
        self,
        strategy_id: str = "stub_strategy_v1",
        price_change_threshold: float = 0.0,
    ) -> None:
        if price_change_threshold < 0:
            raise ValueError("price_change_threshold must be non-negative")

        self._strategy_id = strategy_id
        self._price_change_threshold = price_change_threshold

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
        price_delta = current_close - previous_close

        if price_delta > self._price_change_threshold:
            direction = SignalDirection.BUY
            confidence = 0.55
        elif price_delta < -self._price_change_threshold:
            direction = SignalDirection.SELL
            confidence = 0.55
        else:
            return None

        signal_id = self._build_signal_id(
            run_id=context.run_id,
            strategy_id=context.strategy_id,
            symbol=context.symbol,
            bar_timestamp=context.bar_timestamp,
            direction=direction,
            previous_close=previous_close,
            current_close=current_close,
        )

        return Signal(
            signal_id=signal_id,
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
                "price_delta": price_delta,
                "price_change_threshold": self._price_change_threshold,
            },
        )

    @staticmethod
    def _build_signal_id(
        *,
        run_id: UUID,
        strategy_id: str,
        symbol: str,
        bar_timestamp: Any,
        direction: SignalDirection,
        previous_close: float,
        current_close: float,
    ) -> UUID:
        seed = (
            f"run_id={run_id}|"
            f"strategy_id={strategy_id}|"
            f"symbol={symbol}|"
            f"bar_timestamp={bar_timestamp.isoformat()}|"
            f"direction={direction.value}|"
            f"previous_close={previous_close:.10f}|"
            f"current_close={current_close:.10f}"
        )
        return uuid5(NAMESPACE_URL, seed)

    @staticmethod
    def _get_close(bar: Any) -> float:
        if hasattr(bar, "close"):
            return float(bar.close)

        if isinstance(bar, dict) and "close" in bar:
            return float(bar["close"])

        raise ValueError("Bar object does not expose a close price.")

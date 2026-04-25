from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
    extract_closes,
)
from autonomous_trading_platform.strategy.indicators.mean_reversion import z_score


class MeanReversionStrategy(BaseStrategy):
    def __init__(
        self,
        strategy_id: str,
        window: int = 20,
        buy_below_z: float = -2.0,
        sell_above_z: float = 2.0,
    ) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if buy_below_z >= sell_above_z:
            raise ValueError("buy_below_z must be less than sell_above_z")

        self._strategy_id = strategy_id
        self.window = window
        self.buy_below_z = buy_below_z
        self.sell_above_z = sell_above_z

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        closes = extract_closes(context.bars)
        current_z_score = z_score(closes, self.window)

        if current_z_score is None:
            return None

        if current_z_score <= self.buy_below_z:
            direction = SignalDirection.BUY
            confidence = min(abs(current_z_score / self.buy_below_z), 1.0)
            reason = "z_score_below_buy_threshold"
        elif current_z_score >= self.sell_above_z:
            direction = SignalDirection.SELL
            confidence = min(abs(current_z_score / self.sell_above_z), 1.0)
            reason = "z_score_above_sell_threshold"
        else:
            return None

        params = {
            "strategy_type": "mean_reversion",
            "window": self.window,
            "z_score": current_z_score,
            "buy_below_z": self.buy_below_z,
            "sell_above_z": self.sell_above_z,
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
            confidence=confidence,
            params=params,
        )

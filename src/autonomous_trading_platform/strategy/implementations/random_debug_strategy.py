from __future__ import annotations

import random

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.base_strategy_helpers import (
    build_signal_id,
)


class RandomStrategy(BaseStrategy):
    """
    Random baseline strategy.

    Purpose:
    - Provide a neutral benchmark for simulation validation.
    - Should have ~0 edge before costs and negative performance after costs.

    Behavior:
    - Emits BUY / SELL / None randomly.
    - Deterministic when random_seed is fixed.
    """

    def __init__(
        self,
        strategy_id: str,
        signal_probability: float = 0.33,
        buy_probability: float = 0.5,
        random_seed: int | None = None,
    ) -> None:
        """
        Args:
            strategy_id: unique identifier
            signal_probability: probability of emitting ANY signal (vs None)
            buy_probability: probability of BUY given a signal (else SELL)
            random_seed: ensures deterministic execution
        """
        if not (0.0 <= signal_probability <= 1.0):
            raise ValueError("signal_probability must be in [0, 1]")

        if not (0.0 <= buy_probability <= 1.0):
            raise ValueError("buy_probability must be in [0, 1]")

        self._strategy_id = strategy_id
        self.signal_probability = signal_probability
        self.buy_probability = buy_probability

        # deterministic RNG (important for TASK-166)
        self._rng = random.Random(random_seed)

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        # Step 1: decide whether to emit a signal at all
        if self._rng.random() > self.signal_probability:
            return None

        # Step 2: decide direction
        if self._rng.random() < self.buy_probability:
            direction = SignalDirection.BUY
        else:
            direction = SignalDirection.SELL

        # Step 3: fixed confidence (no edge)
        confidence = 0.5

        params = {
            "strategy_type": "random",
            "signal_probability": self.signal_probability,
            "buy_probability": self.buy_probability,
            "random_seeded": True,
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

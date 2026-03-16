from __future__ import annotations

from abc import ABC, abstractmethod

from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext


class BaseStrategy(ABC):
    """
    Base interface for all strategy implementations.

    A strategy must:
    - evaluate using only data available at bar close
    - be deterministic for the same inputs
    - return either one Signal or None for a symbol evaluation
    """

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """
        Unique identifier for this strategy instance/type.
        """
        raise NotImplementedError

    @abstractmethod
    def evaluate_symbol(self, context: StrategyContext) -> Signal | None:
        """
        Evaluate one symbol at one completed bar timestamp.

        Args:
            context: Strategy evaluation context containing the symbol,
                completed bars, evaluation timestamp, and optional config/state.

        Returns:
            A Signal if the strategy wants to act, otherwise None.
        """
        raise NotImplementedError

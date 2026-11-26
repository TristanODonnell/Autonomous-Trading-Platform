"""Planted controls: strategies whose answer is known, used to test the research pipeline.

Research plan, top board "Planted controls, M1" (decided by the user 2026-11-25). A
positive control is given the run's future on purpose and must be found by the pipeline;
a negative control has no edge and must be rejected. Neither is a trading strategy.

This is the one exception to "a strategy sees only bars before the evaluation time".
The simulator hands the run's window to a strategy only when it is an instance of
``PlantedControlStrategy`` (``SimulationExecutionEngine.execute``); every other strategy
is untouched and still reads its bars through the lookahead guard. A control that was
never handed its window raises instead of trading, so one started anywhere else (the
live trading cycle builds contexts from Parquet and never binds a window) cannot trade.
Controls are registered ``debug=True, production_ready=False``, so the generation draws
and the production lists leave them out, and research never seeds governance from them
(``is_planted_control_type``).
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy


class PlantedControlStrategy(BaseStrategy):
    """Base class of every planted control. The simulator checks for this class only."""

    @abstractmethod
    def bind_run_window(self, window: Any) -> None:
        """Receive the run's whole window (``SimulationWindowData``) before the first bar."""
        raise NotImplementedError


class PlantedControlNotBoundError(RuntimeError):
    """A planted control was asked for a signal without having been handed its window."""


def is_planted_control_type(strategy_type: str) -> bool:
    """True when the registered strategy type is a planted control."""
    from autonomous_trading_platform.strategy.registry import get_registry

    registry = get_registry()
    if not registry.strategy_exists(strategy_type):
        return False
    implementation = registry.get_definition(strategy_type).implementation_class
    return isinstance(implementation, type) and issubclass(implementation, PlantedControlStrategy)

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationSummary,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class BaseStrategyGenerator(ABC):
    """Yields StrategyConfig objects for a given strategy type and parameter space."""

    def __init__(self) -> None:
        self.last_summary = GenerationSummary()

    @abstractmethod
    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list] | None = None,
        options: GenerationOptions | None = None,
    ) -> Iterator[StrategyConfig]:
        raise NotImplementedError

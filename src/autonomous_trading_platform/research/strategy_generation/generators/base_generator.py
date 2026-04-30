from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class BaseStrategyGenerator(ABC):
    """Yields StrategyConfig objects for a given strategy type and parameter space.

    Generators are pure computation — no I/O, no DB access.
    Deduplication via config_hash is handled by StrategyGenerationEngine.
    """

    @abstractmethod
    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> Iterator[StrategyConfig]:
        raise NotImplementedError

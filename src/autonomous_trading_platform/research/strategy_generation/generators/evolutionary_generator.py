from __future__ import annotations

from collections.abc import Iterator

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_generator import BaseStrategyGenerator


class EvolutionaryGenerator(BaseStrategyGenerator):
    """Placeholder for future evolutionary strategy generation.

    Will support mutation, crossover and selection across generations.
    Not implemented — use GridSearchGenerator or RandomSamplingGenerator.
    """

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> Iterator[StrategyConfig]:
        raise NotImplementedError(
            "EvolutionaryGenerator is not yet implemented. "
            "Use GridSearchGenerator or RandomSamplingGenerator."
        )

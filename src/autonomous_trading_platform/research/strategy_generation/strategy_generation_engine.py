from __future__ import annotations

from autonomous_trading_platform.research.strategy_generation.generators.base_generator import (
    BaseStrategyGenerator,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


class StrategyGenerationEngine:
    def __init__(self, generator: BaseStrategyGenerator) -> None:
        self.generator = generator

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> list[StrategyConfig]:
        seen: set[str] = set()
        configs: list[StrategyConfig] = []

        for config in self.generator.generate(strategy_type, parameter_space):
            if config.config_hash() not in seen:
                seen.add(config.config_hash())
                configs.append(config)

        return configs

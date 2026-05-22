from __future__ import annotations

from collections.abc import Iterator
from itertools import product

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationSummary,
)
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    ParameterSpaceResolver,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_generator import BaseStrategyGenerator
from .utils import make_config


class GridSearchGenerator(BaseStrategyGenerator):
    def __init__(self, resolver: ParameterSpaceResolver | None = None) -> None:
        super().__init__()
        self.resolver = resolver or ParameterSpaceResolver()

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list] | None = None,
        options: GenerationOptions | None = None,
    ) -> Iterator[StrategyConfig]:
        self.last_summary = GenerationSummary()
        resolved_space = self.resolver.resolve(strategy_type, parameter_space)

        if not resolved_space:
            self.last_summary.generated_count += 1
            try:
                yield make_config(strategy_type, {})
            except ValueError as exc:
                self.last_summary.reject(
                    str(exc),
                    strategy_type=strategy_type,
                    parameters={},
                    generator="grid",
                )
            return

        sorted_keys = sorted(resolved_space.keys())
        sorted_value_lists = [resolved_space[k] for k in sorted_keys]

        for combo in product(*sorted_value_lists):
            self.last_summary.generated_count += 1
            params = dict(zip(sorted_keys, combo, strict=True))
            try:
                yield make_config(strategy_type, params)
            except ValueError as exc:
                self.last_summary.reject(
                    str(exc),
                    strategy_type=strategy_type,
                    parameters=params,
                    generator="grid",
                )

from __future__ import annotations

import random
from collections.abc import Iterator

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


class RandomSamplingGenerator(BaseStrategyGenerator):
    def __init__(
        self,
        n_samples: int = 50,
        seed: int = 42,
        resolver: ParameterSpaceResolver | None = None,
    ) -> None:
        super().__init__()
        self.n_samples = n_samples
        self.seed = seed
        self.resolver = resolver or ParameterSpaceResolver()

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list] | None = None,
        options: GenerationOptions | None = None,
    ) -> Iterator[StrategyConfig]:
        self.last_summary = GenerationSummary()
        options = options or GenerationOptions(seed=self.seed, n_samples=self.n_samples)
        n_samples = options.n_samples if options.n_samples is not None else self.n_samples
        seed = options.seed if options.seed is not None else self.seed
        rng = random.Random(seed)
        resolved_space = self.resolver.resolve(strategy_type, parameter_space)

        if not resolved_space:
            for _ in range(n_samples):
                self.last_summary.generated_count += 1
                try:
                    yield make_config(strategy_type, {})
                except ValueError as exc:
                    self.last_summary.reject(
                        str(exc),
                        strategy_type=strategy_type,
                        parameters={},
                        generator="random",
                    )
            return

        sorted_keys = sorted(resolved_space)
        for _ in range(n_samples):
            self.last_summary.generated_count += 1
            params = {key: rng.choice(resolved_space[key]) for key in sorted_keys}
            try:
                yield make_config(strategy_type, params)
            except ValueError as exc:
                self.last_summary.reject(
                    str(exc),
                    strategy_type=strategy_type,
                    parameters=params,
                    generator="random",
                )

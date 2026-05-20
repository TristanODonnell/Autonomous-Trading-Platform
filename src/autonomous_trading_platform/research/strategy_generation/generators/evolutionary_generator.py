from __future__ import annotations

import random
from collections.abc import Iterator
from copy import deepcopy
from typing import Any

from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
    GenerationSummary,
)
from autonomous_trading_platform.research.strategy_generation.parameter_space_resolver import (
    ParameterSpaceResolver,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import get_registry

from .base_generator import BaseStrategyGenerator
from .utils import make_config


class EvolutionaryGenerator(BaseStrategyGenerator):
    """Minimal deterministic mutation-driven candidate generator."""

    def __init__(
        self,
        *,
        seed: int = 42,
        population_size: int = 20,
        generations: int = 3,
        mutation_rate: float = 0.25,
        resolver: ParameterSpaceResolver | None = None,
    ) -> None:
        super().__init__()
        self.seed = seed
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.resolver = resolver or ParameterSpaceResolver()

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list] | None = None,
        options: GenerationOptions | None = None,
    ) -> Iterator[StrategyConfig]:
        self.last_summary = GenerationSummary()
        options = options or GenerationOptions(
            seed=self.seed,
            population_size=self.population_size,
            generations=self.generations,
            mutation_rate=self.mutation_rate,
        )
        rng = random.Random(options.seed)
        resolved_space = self.resolver.resolve(strategy_type, parameter_space)
        defaults = get_registry().get_default_parameters(strategy_type)
        tunable_keys = sorted(resolved_space)

        population = self._initial_population(
            defaults=defaults,
            resolved_space=resolved_space,
            population_size=options.population_size,
            rng=rng,
        )

        for params in population:
            config = self._try_make(strategy_type, params)
            if config is not None:
                yield config

        for _ in range(options.generations):
            next_population: list[dict[str, Any]] = []
            for parent in population:
                child = deepcopy(parent)
                for key in tunable_keys:
                    if rng.random() <= options.mutation_rate:
                        child[key] = rng.choice(resolved_space[key])
                next_population.append(child)
                config = self._try_make(strategy_type, child)
                if config is not None:
                    yield config
            population = next_population

    def _initial_population(
        self,
        *,
        defaults: dict[str, Any],
        resolved_space: dict[str, list[Any]],
        population_size: int,
        rng: random.Random,
    ) -> list[dict[str, Any]]:
        population: list[dict[str, Any]] = [dict(defaults)]
        keys = sorted(resolved_space)
        for _ in range(max(population_size - 1, 0)):
            candidate = dict(defaults)
            for key in keys:
                candidate[key] = rng.choice(resolved_space[key])
            population.append(candidate)
        return population

    def _try_make(self, strategy_type: str, params: dict[str, Any]) -> StrategyConfig | None:
        self.last_summary.generated_count += 1
        try:
            return make_config(strategy_type, params)
        except ValueError as exc:
            self.last_summary.reject(
                str(exc),
                strategy_type=strategy_type,
                parameters=params,
                generator="evolutionary",
            )
            return None

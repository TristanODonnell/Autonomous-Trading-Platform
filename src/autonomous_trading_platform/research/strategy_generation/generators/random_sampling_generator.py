from __future__ import annotations

import random
from collections.abc import Iterator

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_generator import BaseStrategyGenerator
from .utils import make_config


class RandomSamplingGenerator(BaseStrategyGenerator):
    def __init__(self, n_samples: int, seed: int) -> None:
        self.n_samples = n_samples
        self.seed = seed

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> Iterator[StrategyConfig]:
        rng = random.Random(self.seed)

        for _ in range(self.n_samples):
            params = {key: rng.choice(values) for key, values in parameter_space.items()}
            yield make_config(strategy_type, params)

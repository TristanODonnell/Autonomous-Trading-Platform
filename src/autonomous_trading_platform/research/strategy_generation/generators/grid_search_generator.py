from __future__ import annotations

from collections.abc import Iterator
from itertools import product

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_generator import BaseStrategyGenerator
from .utils import make_config


class GridSearchGenerator(BaseStrategyGenerator):
    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> Iterator[StrategyConfig]:
        if not parameter_space:
            yield make_config(strategy_type, {})
            return

        sorted_keys = sorted(parameter_space.keys())
        sorted_value_lists = [parameter_space[k] for k in sorted_keys]

        for combo in product(*sorted_value_lists):
            params = dict(zip(sorted_keys, combo, strict=True))
            yield make_config(strategy_type, params)

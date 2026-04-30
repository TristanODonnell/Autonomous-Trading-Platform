from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from itertools import product

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

from .base_generator import BaseStrategyGenerator


class GridSearchGenerator(BaseStrategyGenerator):
    """Deterministic grid search over all combinations of parameter values.

    Given a parameter space like:
        {
            "short_window": [5, 10, 20],
            "long_window":  [50, 100, 200],
        }
    produces every combination — 9 configs in that example — in a stable,
    sorted order so the same input always yields the same sequence.

    Usage:
        gen = GridSearchGenerator()
        configs = list(gen.generate("moving_average_crossover", param_space))
    """

    def generate(
        self,
        strategy_type: str,
        parameter_space: dict[str, list],
    ) -> Iterator[StrategyConfig]:
        if not parameter_space:
            yield self._make_config(strategy_type, {})
            return

        # Sort keys so iteration order is deterministic regardless of dict insertion order
        sorted_keys = sorted(parameter_space.keys())
        sorted_value_lists = [parameter_space[k] for k in sorted_keys]

        for combo in product(*sorted_value_lists):
            params = dict(zip(sorted_keys, combo, strict=True))
            yield self._make_config(strategy_type, params)

    def _make_config(
        self,
        strategy_type: str,
        params: dict,
    ) -> StrategyConfig:
        config_hash = _compute_config_hash(strategy_type, params)
        strategy_id = f"{strategy_type}__{config_hash}"
        return StrategyConfig(
            strategy_id=strategy_id,
            type=strategy_type,
            parameters=params,
            config_hash=config_hash,
        )


def _compute_config_hash(strategy_type: str, params: dict) -> str:
    """Stable, deterministic hash of a strategy type + parameter dict.

    sort_keys=True ensures {"a": 1, "b": 2} and {"b": 2, "a": 1} hash identically.
    Truncated to 12 hex chars — collision risk negligible at the scale of 1000+ configs.
    """
    payload = {"type": strategy_type, "parameters": params}
    serialised = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:12]

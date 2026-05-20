from __future__ import annotations

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import get_registry


def make_config(strategy_type: str, params: dict) -> StrategyConfig:
    normalized = get_registry().normalize_parameters(strategy_type, params)
    config = StrategyConfig(
        strategy_id="temp",  # placeholder
        type=strategy_type,
        parameters=normalized,
    )
    hash_value = config.config_hash()
    return StrategyConfig(
        strategy_id=f"{strategy_type}__{hash_value}",
        type=strategy_type,
        parameters=normalized,
    )

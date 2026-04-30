from __future__ import annotations

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def make_config(strategy_type: str, params: dict) -> StrategyConfig:
    config = StrategyConfig(
        strategy_id="temp",  # placeholder
        type=strategy_type,
        parameters=params,
    )
    hash_value = config.config_hash()
    return StrategyConfig(
        strategy_id=f"{strategy_type}__{hash_value}",
        type=strategy_type,
        parameters=params,
    )

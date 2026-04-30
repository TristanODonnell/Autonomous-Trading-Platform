from __future__ import annotations

import hashlib
import json

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def compute_config_hash(strategy_type: str, params: dict) -> str:
    """Stable, deterministic hash of a strategy type + parameter dict.

    sort_keys=True ensures {"a": 1, "b": 2} and {"b": 2, "a": 1} hash identically.
    Truncated to 12 hex chars — collision risk negligible at the scale of 1000+ configs.
    """
    payload = {"type": strategy_type, "parameters": params}
    serialised = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialised.encode()).hexdigest()[:12]


def make_config(strategy_type: str, params: dict) -> StrategyConfig:
    config_hash = compute_config_hash(strategy_type, params)
    return StrategyConfig(
        strategy_id=f"{strategy_type}__{config_hash}",
        type=strategy_type,
        parameters=params,
        config_hash=config_hash,
    )

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.registry import get_registry


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_strategy_config_omitted_parameters_become_schema_defaults(strategy_type: str) -> None:
    cfg = StrategyConfig(type=strategy_type, strategy_id=f"{strategy_type}-test")
    assert cfg.parameters == get_registry().get_default_parameters(strategy_type)


def test_strategy_config_rejects_unknown_parameters() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        StrategyConfig(type="momentum", strategy_id="bad", parameters={"unknown": 1})


def test_strategy_config_rejects_invalid_parameters() -> None:
    with pytest.raises(ValidationError, match="short_window"):
        StrategyConfig(
            type="moving_average_crossover",
            strategy_id="bad",
            parameters={"short_window": 50, "long_window": 10},
        )


def test_config_hash_is_stable_for_equivalent_normalized_configs() -> None:
    explicit = StrategyConfig(
        type="momentum",
        strategy_id="momentum-test",
        parameters={"lookback": 5, "buy_above": 0, "sell_below": 0},
    )
    omitted = StrategyConfig(type="momentum", strategy_id="momentum-test")

    assert explicit.parameters == omitted.parameters
    assert explicit.canonical_json() == omitted.canonical_json()
    assert explicit.config_hash() == omitted.config_hash()


def test_config_hash_is_independent_of_parameter_order() -> None:
    first = StrategyConfig(
        type="factor_based",
        strategy_id="factor-test",
        parameters={
            "momentum_lookback": 5,
            "mean_reversion_window": 20,
            "volatility_window": 20,
            "volume_window": 20,
            "buy_score_threshold": 0.5,
            "sell_score_threshold": -0.5,
            "momentum_weight": 0.4,
            "mean_reversion_weight": 0.3,
            "volume_weight": 0.2,
            "volatility_weight": 0.1,
        },
    )
    second = StrategyConfig(
        type="factor_based",
        strategy_id="factor-test",
        parameters={
            "volatility_weight": 0.1,
            "volume_weight": 0.2,
            "mean_reversion_weight": 0.3,
            "momentum_weight": 0.4,
            "sell_score_threshold": -0.5,
            "buy_score_threshold": 0.5,
            "volume_window": 20,
            "volatility_window": 20,
            "mean_reversion_window": 20,
            "momentum_lookback": 5,
        },
    )

    assert first.parameters == second.parameters
    assert first.config_hash() == second.config_hash()


def test_distinct_normalized_configs_have_distinct_hashes() -> None:
    first = StrategyConfig(
        type="momentum",
        strategy_id="momentum-test",
        parameters={"lookback": 5},
    )
    second = StrategyConfig(
        type="momentum",
        strategy_id="momentum-test",
        parameters={"lookback": 6},
    )

    assert first.config_hash() != second.config_hash()

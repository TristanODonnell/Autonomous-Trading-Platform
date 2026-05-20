from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.implementations.mean_reversion_strategy import (
    MeanReversionStrategy,
)
from autonomous_trading_platform.strategy.registry import get_registry


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_factory_builds_every_strategy_from_normalized_defaults(strategy_type: str) -> None:
    config = StrategyConfig(type=strategy_type, strategy_id=f"{strategy_type}-factory")
    strategy = StrategyFactory().build(config)

    assert isinstance(strategy, get_registry().get_definition(strategy_type).implementation_class)


def test_factory_receives_normalized_parameter_types() -> None:
    config = StrategyConfig(
        type="mean_reversion",
        strategy_id="mean-reversion-factory",
        parameters={"buy_below_z": -2, "sell_above_z": 2},
    )
    strategy = StrategyFactory().build(config)

    assert isinstance(strategy, MeanReversionStrategy)
    assert strategy.window == 20
    assert strategy.buy_below_z == -2.0
    assert strategy.sell_above_z == 2.0


def test_invalid_parameters_fail_before_factory_construction() -> None:
    with pytest.raises(ValidationError, match="lookback"):
        StrategyConfig(type="momentum", strategy_id="bad", parameters={"lookback": 0})

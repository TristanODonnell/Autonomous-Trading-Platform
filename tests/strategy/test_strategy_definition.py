"""Tests for StrategyDefinition metadata correctness.

Covers: core identity fields, warmup metadata, indicator requirements,
parameter defaults, parameter validator integration, compatibility flags,
and builder integration with StrategyFactory.
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.factories.strategy_factory import StrategyFactory
from autonomous_trading_platform.strategy.implementations.base_strategy import BaseStrategy
from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)
from autonomous_trading_platform.strategy.implementations.intentional_loser_strategy import (
    IntentionalLoserStrategy,
)
from autonomous_trading_platform.strategy.implementations.mean_reversion_strategy import (
    MeanReversionStrategy,
)
from autonomous_trading_platform.strategy.implementations.momentum_strategy import MomentumStrategy
from autonomous_trading_platform.strategy.implementations.moving_average_crossover_strategy import (
    MovingAverageCrossoverStrategy,
)
from autonomous_trading_platform.strategy.implementations.random_debug_strategy import (
    RandomStrategy,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from autonomous_trading_platform.strategy.registry import get_registry

# ---------------------------------------------------------------------------
# Core identity fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_display_name_non_empty(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.display_name.strip()


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_description_non_empty(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.description.strip()


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_strategy_type_matches_key(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.strategy_type == strategy_type


# ---------------------------------------------------------------------------
# Implementation class correctness
# ---------------------------------------------------------------------------


_EXPECTED_CLASSES = {
    "stub": StubStrategy,
    "intentional_loser": IntentionalLoserStrategy,
    "random": RandomStrategy,
    "moving_average_crossover": MovingAverageCrossoverStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "factor_based": FactorBasedStrategy,
}


@pytest.mark.parametrize("strategy_type,expected_class", _EXPECTED_CLASSES.items())
def test_implementation_class_is_correct(strategy_type: str, expected_class: type) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.implementation_class is expected_class


# ---------------------------------------------------------------------------
# Warmup bar metadata
# ---------------------------------------------------------------------------


def test_stub_warmup_is_one() -> None:
    defn = get_registry().get_definition("stub")
    assert defn.compute_warmup_bars() == 1


def test_intentional_loser_warmup_is_one() -> None:
    defn = get_registry().get_definition("intentional_loser")
    assert defn.compute_warmup_bars() == 1


def test_random_warmup_is_zero() -> None:
    defn = get_registry().get_definition("random")
    assert defn.compute_warmup_bars() == 0


def test_moving_average_crossover_warmup_default() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    # default long_window=30, needs long_window+1 bars
    assert defn.compute_warmup_bars() == 31


def test_moving_average_crossover_warmup_custom() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    assert defn.compute_warmup_bars({"short_window": 5, "long_window": 50}) == 51


def test_momentum_warmup_default() -> None:
    defn = get_registry().get_definition("momentum")
    # default lookback=5, needs lookback+1 bars
    assert defn.compute_warmup_bars() == 6


def test_momentum_warmup_custom() -> None:
    defn = get_registry().get_definition("momentum")
    assert defn.compute_warmup_bars({"lookback": 20}) == 21


def test_mean_reversion_warmup_default() -> None:
    defn = get_registry().get_definition("mean_reversion")
    # default window=20
    assert defn.compute_warmup_bars() == 20


def test_mean_reversion_warmup_custom() -> None:
    defn = get_registry().get_definition("mean_reversion")
    assert defn.compute_warmup_bars({"window": 50}) == 50


def test_factor_based_warmup_default() -> None:
    defn = get_registry().get_definition("factor_based")
    # max(5+1, 20, 20, 20) = 20
    assert defn.compute_warmup_bars() == 20


def test_factor_based_warmup_custom_large_lookback() -> None:
    defn = get_registry().get_definition("factor_based")
    # momentum_lookback=100 dominates (100+1=101)
    assert (
        defn.compute_warmup_bars(
            {
                "momentum_lookback": 100,
                "mean_reversion_window": 20,
                "volatility_window": 20,
                "volume_window": 20,
            }
        )
        == 101
    )


# ---------------------------------------------------------------------------
# Required indicators
# ---------------------------------------------------------------------------


def test_stub_requires_no_indicators() -> None:
    defn = get_registry().get_definition("stub")
    assert defn.required_indicators == ()


def test_intentional_loser_requires_no_indicators() -> None:
    defn = get_registry().get_definition("intentional_loser")
    assert defn.required_indicators == ()


def test_random_requires_no_indicators() -> None:
    defn = get_registry().get_definition("random")
    assert defn.required_indicators == ()


def test_moving_average_crossover_requires_sma() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    assert "simple_moving_average" in defn.required_indicators


def test_momentum_requires_momentum_indicator() -> None:
    defn = get_registry().get_definition("momentum")
    assert "momentum" in defn.required_indicators


def test_mean_reversion_requires_z_score() -> None:
    defn = get_registry().get_definition("mean_reversion")
    assert "z_score" in defn.required_indicators


def test_factor_based_requires_all_factor_indicators() -> None:
    defn = get_registry().get_definition("factor_based")
    expected = {"momentum", "z_score", "rolling_standard_deviation", "volume_ratio"}
    assert expected.issubset(set(defn.required_indicators))


# ---------------------------------------------------------------------------
# Parameter defaults
# ---------------------------------------------------------------------------


def test_default_parameters_non_empty_for_parameterized_strategies() -> None:
    for strategy_type in ("moving_average_crossover", "momentum", "mean_reversion", "factor_based"):
        defn = get_registry().get_definition(strategy_type)
        assert defn.default_parameters


def test_moving_average_crossover_defaults() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    assert defn.default_parameters["short_window"] == 10
    assert defn.default_parameters["long_window"] == 30


def test_momentum_defaults() -> None:
    defn = get_registry().get_definition("momentum")
    assert defn.default_parameters["lookback"] == 5


def test_mean_reversion_defaults() -> None:
    defn = get_registry().get_definition("mean_reversion")
    assert defn.default_parameters["window"] == 20
    assert defn.default_parameters["buy_below_z"] == -2.0
    assert defn.default_parameters["sell_above_z"] == 2.0


# ---------------------------------------------------------------------------
# Parameter validator integration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_default_parameters_pass_validation(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    # Must not raise
    defn.parameter_validator(defn.default_parameters)


def test_validator_rejects_bad_mac_parameters() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    with pytest.raises(ValueError, match="short_window.*must be strictly less"):
        defn.parameter_validator({"short_window": 50, "long_window": 10})


def test_validator_rejects_bad_momentum_parameters() -> None:
    defn = get_registry().get_definition("momentum")
    with pytest.raises(ValueError, match="lookback must be > 0"):
        defn.parameter_validator({"lookback": 0})


# ---------------------------------------------------------------------------
# Determinism flag
# ---------------------------------------------------------------------------


def test_random_strategy_is_non_deterministic() -> None:
    defn = get_registry().get_definition("random")
    assert not defn.deterministic


def test_all_non_random_strategies_are_deterministic() -> None:
    for defn in get_registry().list_definitions():
        if defn.strategy_type != "random":
            assert defn.deterministic, f"{defn.strategy_type} should be deterministic"


# ---------------------------------------------------------------------------
# Builder integration — StrategyFactory uses registry builder
# ---------------------------------------------------------------------------


factory = StrategyFactory()


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_factory_builds_correct_class(strategy_type: str) -> None:
    config = StrategyConfig(type=strategy_type, strategy_id=f"{strategy_type}-test")
    strategy = factory.build(config)
    expected_class = get_registry().get_definition(strategy_type).implementation_class
    assert isinstance(strategy, expected_class)


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_factory_result_is_base_strategy(strategy_type: str) -> None:
    config = StrategyConfig(type=strategy_type, strategy_id=f"{strategy_type}-test")
    strategy = factory.build(config)
    assert isinstance(strategy, BaseStrategy)


def test_factory_passes_strategy_id() -> None:
    config = StrategyConfig(type="momentum", strategy_id="my-momentum-123")
    strategy = factory.build(config)
    assert strategy.strategy_id == "my-momentum-123"


def test_factory_passes_parameters_to_builder() -> None:
    config = StrategyConfig(
        type="moving_average_crossover",
        strategy_id="mac-test",
        parameters={"short_window": 7, "long_window": 21},
    )
    strategy = factory.build(config)
    assert isinstance(strategy, MovingAverageCrossoverStrategy)
    assert strategy.short_window == 7
    assert strategy.long_window == 21

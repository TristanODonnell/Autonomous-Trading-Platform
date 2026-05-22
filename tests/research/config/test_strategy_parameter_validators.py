"""Tests for per-strategy parameter validators."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.research.config.strategy_parameter_validators import (
    validate_strategy_parameters,
)

# ---------------------------------------------------------------------------
# moving_average_crossover
# ---------------------------------------------------------------------------


def test_mac_valid():
    validate_strategy_parameters(
        "moving_average_crossover", {"short_window": 10, "long_window": 30}
    )


def test_mac_short_equals_long_raises():
    with pytest.raises(ValueError, match="short_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": 20, "long_window": 20}
        )


def test_mac_short_greater_than_long_raises():
    with pytest.raises(ValueError, match="short_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": 50, "long_window": 20}
        )


def test_mac_zero_short_window_raises():
    with pytest.raises(ValueError, match="short_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": 0, "long_window": 30}
        )


def test_mac_negative_short_window_raises():
    with pytest.raises(ValueError, match="short_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": -5, "long_window": 30}
        )


def test_mac_zero_long_window_raises():
    with pytest.raises(ValueError, match="long_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": 10, "long_window": 0}
        )


def test_mac_empty_params_passes():
    # Absent params are not validated — only present ones are checked
    validate_strategy_parameters("moving_average_crossover", {})


def test_mac_only_short_window_provided():
    validate_strategy_parameters("moving_average_crossover", {"short_window": 10})


def test_mac_float_short_window_raises():
    with pytest.raises(ValueError, match="short_window"):
        validate_strategy_parameters(
            "moving_average_crossover", {"short_window": 10.5, "long_window": 30}
        )


# ---------------------------------------------------------------------------
# momentum
# ---------------------------------------------------------------------------


def test_momentum_valid():
    validate_strategy_parameters("momentum", {"lookback": 5})


def test_momentum_zero_lookback_raises():
    with pytest.raises(ValueError, match="lookback"):
        validate_strategy_parameters("momentum", {"lookback": 0})


def test_momentum_negative_lookback_raises():
    with pytest.raises(ValueError, match="lookback"):
        validate_strategy_parameters("momentum", {"lookback": -1})


def test_momentum_empty_params_passes():
    validate_strategy_parameters("momentum", {})


# ---------------------------------------------------------------------------
# mean_reversion
# ---------------------------------------------------------------------------


def test_mean_reversion_valid():
    validate_strategy_parameters(
        "mean_reversion", {"window": 20, "buy_below_z": -2.0, "sell_above_z": 2.0}
    )


def test_mean_reversion_zero_window_raises():
    with pytest.raises(ValueError, match="window"):
        validate_strategy_parameters("mean_reversion", {"window": 0})


def test_mean_reversion_buy_z_equals_sell_z_raises():
    with pytest.raises(ValueError, match="buy_below_z"):
        validate_strategy_parameters("mean_reversion", {"buy_below_z": 0.0, "sell_above_z": 0.0})


def test_mean_reversion_buy_z_above_sell_z_raises():
    with pytest.raises(ValueError, match="buy_below_z"):
        validate_strategy_parameters("mean_reversion", {"buy_below_z": 1.0, "sell_above_z": -1.0})


def test_mean_reversion_only_window_provided():
    validate_strategy_parameters("mean_reversion", {"window": 20})


# ---------------------------------------------------------------------------
# factor_based
# ---------------------------------------------------------------------------


def test_factor_based_valid():
    validate_strategy_parameters(
        "factor_based",
        {
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


def test_factor_based_sell_above_buy_raises():
    with pytest.raises(ValueError, match="sell_score_threshold"):
        validate_strategy_parameters(
            "factor_based",
            {"buy_score_threshold": 0.3, "sell_score_threshold": 0.5},
        )


def test_factor_based_sell_equals_buy_raises():
    with pytest.raises(ValueError, match="sell_score_threshold"):
        validate_strategy_parameters(
            "factor_based",
            {"buy_score_threshold": 0.5, "sell_score_threshold": 0.5},
        )


def test_factor_based_zero_momentum_lookback_raises():
    with pytest.raises(ValueError, match="momentum_lookback"):
        validate_strategy_parameters("factor_based", {"momentum_lookback": 0})


def test_factor_based_negative_weight_raises():
    with pytest.raises(ValueError, match="momentum_weight"):
        validate_strategy_parameters("factor_based", {"momentum_weight": -0.1})


def test_factor_based_zero_weight_is_valid():
    validate_strategy_parameters("factor_based", {"momentum_weight": 0.0})


# ---------------------------------------------------------------------------
# random
# ---------------------------------------------------------------------------


def test_random_valid():
    validate_strategy_parameters("random", {"signal_probability": 0.33, "buy_probability": 0.5})


def test_random_probability_above_one_raises():
    with pytest.raises(ValueError, match="signal_probability"):
        validate_strategy_parameters("random", {"signal_probability": 1.5})


def test_random_probability_below_zero_raises():
    with pytest.raises(ValueError, match="buy_probability"):
        validate_strategy_parameters("random", {"buy_probability": -0.1})


def test_random_probability_exactly_zero_is_valid():
    validate_strategy_parameters("random", {"signal_probability": 0.0})


def test_random_probability_exactly_one_is_valid():
    validate_strategy_parameters("random", {"buy_probability": 1.0})


# ---------------------------------------------------------------------------
# intentional_loser / stub
# ---------------------------------------------------------------------------


def test_intentional_loser_valid():
    validate_strategy_parameters("intentional_loser", {"price_change_threshold": 0.01})


def test_intentional_loser_negative_threshold_raises():
    with pytest.raises(ValueError, match="price_change_threshold"):
        validate_strategy_parameters("intentional_loser", {"price_change_threshold": -0.01})


def test_stub_valid():
    validate_strategy_parameters("stub", {"price_change_threshold": 0.0})


def test_stub_negative_threshold_raises():
    with pytest.raises(ValueError, match="price_change_threshold"):
        validate_strategy_parameters("stub", {"price_change_threshold": -1.0})


# ---------------------------------------------------------------------------
# Unknown strategy type — should not raise
# ---------------------------------------------------------------------------


def test_unknown_strategy_type_is_noop():
    # Catalog validation handles existence checking — unknown types here are silently skipped
    validate_strategy_parameters("future_strategy_type", {"foo": "bar"})


# ---------------------------------------------------------------------------
# StrategyConfig integration — parameters validated via model_validator
# ---------------------------------------------------------------------------


def test_strategy_config_validates_parameters():
    from pydantic import ValidationError

    from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

    with pytest.raises(ValidationError, match="short_window"):
        StrategyConfig(
            type="moving_average_crossover",
            strategy_id="test",
            parameters={"short_window": 50, "long_window": 10},
        )


def test_strategy_config_valid_parameters_accepted():
    from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

    cfg = StrategyConfig(
        type="moving_average_crossover",
        strategy_id="test",
        parameters={"short_window": 10, "long_window": 30},
    )
    assert cfg.parameters["short_window"] == 10

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonomous_trading_platform.strategy.registry import (
    FactorBasedParameters,
    IntentionalLoserParameters,
    MeanReversionParameters,
    MomentumParameters,
    MovingAverageCrossoverParameters,
    RandomParameters,
    StubParameters,
)


def test_moving_average_crossover_schema_defaults_and_normalizes() -> None:
    params = MovingAverageCrossoverParameters.model_validate({})
    assert params.canonical_dict() == {"short_window": 10, "long_window": 30}


@pytest.mark.parametrize(
    "payload",
    [
        {"short_window": 0, "long_window": 30},
        {"short_window": 30, "long_window": 30},
        {"short_window": 30, "long_window": 10},
        {"short_window": 10.5, "long_window": 30},
    ],
)
def test_moving_average_crossover_schema_rejects_invalid(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MovingAverageCrossoverParameters.model_validate(payload)


def test_momentum_schema_enforces_ordered_thresholds() -> None:
    assert MomentumParameters.model_validate(
        {"lookback": 10, "buy_above": 0.02, "sell_below": -0.01}
    ).canonical_dict() == {"lookback": 10, "buy_above": 0.02, "sell_below": -0.01}

    with pytest.raises(ValidationError, match="sell_below"):
        MomentumParameters.model_validate({"buy_above": 0.0, "sell_below": 0.01})


def test_mean_reversion_schema_enforces_threshold_order() -> None:
    assert MeanReversionParameters.model_validate({}).canonical_dict() == {
        "window": 20,
        "buy_below_z": -2.0,
        "sell_above_z": 2.0,
    }

    with pytest.raises(ValidationError, match="buy_below_z"):
        MeanReversionParameters.model_validate({"buy_below_z": 1.0, "sell_above_z": -1.0})


def test_factor_based_schema_enforces_ranges_and_order() -> None:
    assert FactorBasedParameters.model_validate({}).canonical_dict() == {
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
    }

    with pytest.raises(ValidationError, match="volatility_window"):
        FactorBasedParameters.model_validate({"volatility_window": 1})
    with pytest.raises(ValidationError, match="momentum_weight"):
        FactorBasedParameters.model_validate({"momentum_weight": -0.1})
    with pytest.raises(ValidationError, match="sell_score_threshold"):
        FactorBasedParameters.model_validate(
            {"sell_score_threshold": 0.5, "buy_score_threshold": 0.5}
        )


def test_random_schema_supports_optional_seed_and_probability_ranges() -> None:
    assert RandomParameters.model_validate({"random_seed": 42}).canonical_dict() == {
        "signal_probability": 0.33,
        "buy_probability": 0.5,
        "random_seed": 42,
    }

    with pytest.raises(ValidationError, match="signal_probability"):
        RandomParameters.model_validate({"signal_probability": 1.1})
    with pytest.raises(ValidationError, match="random_seed"):
        RandomParameters.model_validate({"random_seed": 1.5})


@pytest.mark.parametrize("schema", [StubParameters, IntentionalLoserParameters])
def test_debug_threshold_schemas_reject_negative_values(schema: type[StubParameters]) -> None:
    assert schema.model_validate({}).canonical_dict() == {"price_change_threshold": 0.0}

    with pytest.raises(ValidationError, match="price_change_threshold"):
        schema.model_validate({"price_change_threshold": -0.01})


def test_schemas_reject_unknown_parameters() -> None:
    with pytest.raises(ValidationError, match="extra"):
        MomentumParameters.model_validate({"lookback": 5, "unknown": 1})

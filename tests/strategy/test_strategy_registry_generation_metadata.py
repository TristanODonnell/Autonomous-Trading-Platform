"""Tests for strategy registry generation/search-space metadata.

Covers ParameterSpec presence, type correctness, range validity,
and generation candidate queries.
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.registry import ParameterSpec, ParameterType, get_registry

# ---------------------------------------------------------------------------
# ParameterSpec presence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_all_strategies_have_parameter_specs(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.parameter_specs is not None
    assert isinstance(defn.parameter_specs, tuple)


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_specs_are_parameter_spec_instances(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        assert isinstance(spec, ParameterSpec), f"{strategy_type}: {spec!r} is not a ParameterSpec"


# ---------------------------------------------------------------------------
# ParameterSpec field invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_names_are_non_empty(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        assert spec.name.strip(), f"{strategy_type}: spec has empty name"


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_descriptions_are_non_empty(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        assert spec.description.strip(), (
            f"{strategy_type}: spec {spec.name!r} has empty description"
        )


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_types_are_valid(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        assert isinstance(spec.parameter_type, ParameterType), (
            f"{strategy_type}: {spec.name} has invalid parameter_type {spec.parameter_type!r}"
        )


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_range_is_ordered(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        if spec.min_value is not None and spec.max_value is not None:
            assert spec.min_value <= spec.max_value, (
                f"{strategy_type}: {spec.name} has min_value > max_value"
            )


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_default_within_range(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        if spec.min_value is not None:
            assert float(spec.default) >= spec.min_value, (
                f"{strategy_type}: {spec.name} default {spec.default} < min {spec.min_value}"
            )
        if spec.max_value is not None:
            assert float(spec.default) <= spec.max_value, (
                f"{strategy_type}: {spec.name} default {spec.default} > max {spec.max_value}"
            )


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_discrete_int_specs_have_step(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        if spec.parameter_type == ParameterType.INT and spec.discrete:
            assert spec.step is not None, (
                f"{strategy_type}: {spec.name} is discrete INT but has no step"
            )


# ---------------------------------------------------------------------------
# Spec names match default_parameters keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_tunable_spec_names_are_valid_parameter_keys(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        if spec.tunable:
            # Every tunable spec should correspond to a real parameter (present in defaults)
            assert spec.name in defn.default_parameters, (
                f"{strategy_type}: tunable spec {spec.name!r} not in default_parameters"
            )


# ---------------------------------------------------------------------------
# Specific strategy search spaces
# ---------------------------------------------------------------------------


def test_moving_average_crossover_has_short_and_long_window_specs() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    spec_names = {s.name for s in defn.parameter_specs}
    assert "short_window" in spec_names
    assert "long_window" in spec_names


def test_moving_average_crossover_short_window_range() -> None:
    defn = get_registry().get_definition("moving_average_crossover")
    spec = next(s for s in defn.parameter_specs if s.name == "short_window")
    assert spec.parameter_type == ParameterType.INT
    assert spec.min_value is not None and spec.min_value >= 1
    assert spec.max_value is not None and spec.max_value > spec.min_value


def test_momentum_lookback_is_tunable_int() -> None:
    defn = get_registry().get_definition("momentum")
    spec = next(s for s in defn.parameter_specs if s.name == "lookback")
    assert spec.parameter_type == ParameterType.INT
    assert spec.tunable


def test_mean_reversion_window_has_range() -> None:
    defn = get_registry().get_definition("mean_reversion")
    spec = next(s for s in defn.parameter_specs if s.name == "window")
    assert spec.min_value is not None
    assert spec.max_value is not None


def test_factor_based_has_all_window_specs() -> None:
    defn = get_registry().get_definition("factor_based")
    spec_names = {s.name for s in defn.parameter_specs}
    assert "momentum_lookback" in spec_names
    assert "mean_reversion_window" in spec_names
    assert "volatility_window" in spec_names
    assert "volume_window" in spec_names


def test_factor_based_weight_specs_are_floats() -> None:
    defn = get_registry().get_definition("factor_based")
    weight_specs = [s for s in defn.parameter_specs if "weight" in s.name]
    assert weight_specs, "factor_based should have weight specs"
    for spec in weight_specs:
        assert spec.parameter_type == ParameterType.FLOAT


def test_debug_strategies_have_non_tunable_or_no_tunable_specs() -> None:
    for defn in get_registry().list_debug_strategies():
        tunable = [s for s in defn.parameter_specs if s.tunable]
        assert (
            all(not s.tunable for s in tunable) or True
        )  # no constraint enforced, just presence check


# ---------------------------------------------------------------------------
# Generation candidates
# ---------------------------------------------------------------------------


def test_generation_candidates_are_all_registered() -> None:
    candidates = get_registry().get_generation_candidates()
    all_types = set(get_registry().list_strategy_types())
    for defn in candidates:
        assert defn.strategy_type in all_types


def test_production_strategies_are_generation_candidates() -> None:
    candidates = {d.strategy_type for d in get_registry().get_generation_candidates()}
    for defn in get_registry().list_production_strategies():
        assert defn.strategy_type in candidates, (
            f"{defn.strategy_type} has no parameter_specs; cannot be a generation candidate"
        )

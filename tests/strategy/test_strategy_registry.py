"""Tests for StrategyRegistry lookup, classification, and determinism."""

from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.registry import (
    StrategyDefinition,
    StrategyFamily,
    get_registry,
)
from autonomous_trading_platform.strategy.registry.strategy_registry import StrategyRegistry

# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------


def test_get_registry_returns_same_instance() -> None:
    assert get_registry() is get_registry()


def test_registry_is_locked_after_import() -> None:
    assert get_registry().is_locked()


def test_registry_rejects_registration_after_lock() -> None:
    with pytest.raises(RuntimeError, match="locked"):
        get_registry().register(
            StrategyDefinition(
                strategy_type="should_fail",
                display_name="x",
                description="x",
                family=StrategyFamily.DEBUG,
                implementation_class=object,
                debug=True,
                production_ready=False,
                default_parameters={},
                parameter_validator=lambda p: None,
                warmup_bars_fn=lambda p: 0,
                required_indicators=(),
                required_persisted_features=(),
                parameter_specs=(),
                builder=lambda sid, p: None,
            )
        )


# ---------------------------------------------------------------------------
# Duplicate rejection (fresh registry)
# ---------------------------------------------------------------------------


def test_fresh_registry_rejects_duplicate_registration() -> None:
    reg = StrategyRegistry()
    defn = StrategyDefinition(
        strategy_type="dup",
        display_name="Dup",
        description="duplicate test",
        family=StrategyFamily.DEBUG,
        implementation_class=object,
        debug=True,
        production_ready=False,
        default_parameters={},
        parameter_validator=lambda p: None,
        warmup_bars_fn=lambda p: 0,
        required_indicators=(),
        required_persisted_features=(),
        parameter_specs=(),
        builder=lambda sid, p: None,
    )
    reg.register(defn)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(defn)


# ---------------------------------------------------------------------------
# strategy_exists / get_definition
# ---------------------------------------------------------------------------


_EXPECTED_ALL = {
    "stub",
    "intentional_loser",
    "random",
    "moving_average_crossover",
    "momentum",
    "mean_reversion",
    "factor_based",
    "composite_rule",
}

_EXPECTED_DEBUG = {"stub", "intentional_loser", "random"}
_EXPECTED_PRODUCTION = _EXPECTED_ALL - _EXPECTED_DEBUG


def test_all_expected_strategies_are_registered() -> None:
    assert set(get_registry().list_strategy_types()) == _EXPECTED_ALL


def test_strategy_exists_true_for_known() -> None:
    for t in _EXPECTED_ALL:
        assert get_registry().strategy_exists(t)


def test_strategy_exists_false_for_unknown() -> None:
    assert not get_registry().strategy_exists("not_real")


def test_get_definition_returns_strategy_definition() -> None:
    defn = get_registry().get_definition("momentum")
    assert isinstance(defn, StrategyDefinition)
    assert defn.strategy_type == "momentum"


def test_get_definition_raises_for_unknown() -> None:
    with pytest.raises(KeyError, match="Unknown strategy type"):
        get_registry().get_definition("bogus")


# ---------------------------------------------------------------------------
# Debug / production classification
# ---------------------------------------------------------------------------


def test_debug_strategies_match_expected() -> None:
    debug_types = {d.strategy_type for d in get_registry().list_debug_strategies()}
    assert debug_types == _EXPECTED_DEBUG


def test_production_strategies_match_expected() -> None:
    prod_types = {d.strategy_type for d in get_registry().list_production_strategies()}
    assert prod_types == _EXPECTED_PRODUCTION


def test_debug_and_production_are_disjoint() -> None:
    debug = {d.strategy_type for d in get_registry().list_debug_strategies()}
    prod = {d.strategy_type for d in get_registry().list_production_strategies()}
    assert not (debug & prod)


def test_debug_and_production_union_equals_all() -> None:
    debug = {d.strategy_type for d in get_registry().list_debug_strategies()}
    prod = {d.strategy_type for d in get_registry().list_production_strategies()}
    assert debug | prod == set(get_registry().list_strategy_types())


def test_debug_flag_consistent_with_classification() -> None:
    for defn in get_registry().list_definitions():
        if defn.debug:
            assert defn.strategy_type in _EXPECTED_DEBUG
        else:
            assert defn.strategy_type in _EXPECTED_PRODUCTION


def test_production_ready_flag_consistent() -> None:
    for defn in get_registry().list_production_strategies():
        assert defn.production_ready
    for defn in get_registry().list_debug_strategies():
        assert not defn.production_ready


# ---------------------------------------------------------------------------
# Family classification
# ---------------------------------------------------------------------------


_EXPECTED_FAMILIES = {
    "stub": StrategyFamily.DEBUG,
    "intentional_loser": StrategyFamily.DEBUG,
    "random": StrategyFamily.DEBUG,
    "moving_average_crossover": StrategyFamily.TREND,
    "momentum": StrategyFamily.MOMENTUM,
    "mean_reversion": StrategyFamily.MEAN_REVERSION,
    "factor_based": StrategyFamily.FACTOR,
    "composite_rule": StrategyFamily.COMPOSITE,
}


@pytest.mark.parametrize("strategy_type,expected_family", _EXPECTED_FAMILIES.items())
def test_family_classification(strategy_type: str, expected_family: StrategyFamily) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.family == expected_family


def test_list_families_contains_expected() -> None:
    families = set(get_registry().list_families())
    assert StrategyFamily.DEBUG in families
    assert StrategyFamily.TREND in families
    assert StrategyFamily.MOMENTUM in families
    assert StrategyFamily.MEAN_REVERSION in families
    assert StrategyFamily.FACTOR in families
    assert StrategyFamily.COMPOSITE in families


def test_get_family_strategies_debug() -> None:
    debug = get_registry().get_family_strategies(StrategyFamily.DEBUG)
    assert {d.strategy_type for d in debug} == _EXPECTED_DEBUG


def test_get_family_strategies_trend() -> None:
    trend = get_registry().get_family_strategies(StrategyFamily.TREND)
    assert {d.strategy_type for d in trend} == {"moving_average_crossover"}


def test_get_family_strategies_momentum() -> None:
    momentum = get_registry().get_family_strategies(StrategyFamily.MOMENTUM)
    assert {d.strategy_type for d in momentum} == {"momentum"}


def test_get_family_strategies_composite() -> None:
    composite = get_registry().get_family_strategies(StrategyFamily.COMPOSITE)
    assert {d.strategy_type for d in composite} == {"composite_rule"}


# ---------------------------------------------------------------------------
# Registration order is deterministic
# ---------------------------------------------------------------------------


def test_list_strategy_types_order_is_stable() -> None:
    first = get_registry().list_strategy_types()
    second = get_registry().list_strategy_types()
    assert first == second


def test_registration_order_starts_with_debug() -> None:
    types = get_registry().list_strategy_types()
    # First three registrations are the debug strategies
    assert types[:3] == ["stub", "intentional_loser", "random"]


# ---------------------------------------------------------------------------
# list_definitions completeness
# ---------------------------------------------------------------------------


def test_list_definitions_count_matches_types() -> None:
    assert len(get_registry().list_definitions()) == len(get_registry().list_strategy_types())


def test_list_definitions_all_are_strategy_definition() -> None:
    for defn in get_registry().list_definitions():
        assert isinstance(defn, StrategyDefinition)


# ---------------------------------------------------------------------------
# Generation candidates
# ---------------------------------------------------------------------------


def test_get_generation_candidates_excludes_no_specs() -> None:
    candidates = get_registry().get_generation_candidates()
    # All registered strategies have parameter_specs, so all should be candidates
    assert len(candidates) == len(get_registry().list_definitions())


def test_all_definitions_have_parameter_specs() -> None:
    for defn in get_registry().list_definitions():
        assert defn.parameter_specs is not None


def test_all_registered_strategies_have_complete_metadata_and_valid_defaults() -> None:
    for defn in get_registry().list_definitions():
        assert defn.strategy_type
        assert defn.display_name
        assert defn.description
        assert defn.implementation_class is not None
        assert callable(defn.builder)
        assert callable(defn.warmup_bars_fn)
        assert defn.parameter_schema is not None

        normalized_defaults = defn.normalize_parameters(defn.default_parameters)

        assert normalized_defaults == defn.default_parameters
        assert isinstance(defn.compute_warmup_bars(normalized_defaults), int)
        assert defn.compute_warmup_bars(normalized_defaults) >= 0


def test_parameter_specs_match_schema_defaults() -> None:
    for defn in get_registry().list_definitions():
        schema_fields = set(defn.normalize_parameters({}))
        spec_names = {spec.name for spec in defn.parameter_specs}

        assert spec_names <= schema_fields
        for spec in defn.parameter_specs:
            if spec.default is not None:
                assert (
                    defn.normalize_parameters({spec.name: spec.default})[spec.name] == spec.default
                )

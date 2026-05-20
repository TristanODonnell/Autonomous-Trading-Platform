from __future__ import annotations

from autonomous_trading_platform.strategy.registry import get_registry

EXPECTED_INDICATORS = {
    "stub": (),
    "intentional_loser": (),
    "random": (),
    "moving_average_crossover": ("simple_moving_average",),
    "momentum": ("momentum",),
    "mean_reversion": ("z_score", "simple_moving_average", "rolling_standard_deviation"),
    "factor_based": ("momentum", "z_score", "rolling_standard_deviation", "volume_ratio"),
    "composite_rule": ("momentum",),
}


def test_registered_strategy_indicator_dependencies_are_explicit() -> None:
    registry = get_registry()

    assert set(registry.list_strategy_types()) == set(EXPECTED_INDICATORS)
    for strategy_type, expected in EXPECTED_INDICATORS.items():
        defn = registry.get_definition(strategy_type)
        assert defn.required_indicators == expected


def test_no_current_strategy_declares_persisted_features() -> None:
    for defn in get_registry().list_definitions():
        assert defn.required_persisted_features == ()


def test_debug_strategies_have_no_accidental_feature_dependencies() -> None:
    for defn in get_registry().list_debug_strategies():
        assert defn.required_indicators == ()
        assert defn.required_persisted_features == ()


def test_dependency_metadata_is_deterministic() -> None:
    first = [
        (d.strategy_type, d.required_indicators, d.required_persisted_features)
        for d in get_registry().list_definitions()
    ]
    second = [
        (d.strategy_type, d.required_indicators, d.required_persisted_features)
        for d in get_registry().list_definitions()
    ]

    assert first == second

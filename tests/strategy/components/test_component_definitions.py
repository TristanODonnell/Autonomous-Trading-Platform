from __future__ import annotations

from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry

EXPECTED_INDICATORS = {
    "simple_moving_average",
    "exponential_moving_average",
    "momentum",
    "rate_of_change",
    "rsi",
    "rsi_wilder",
    "z_score",
    "distance_from_moving_average",
    "rolling_standard_deviation",
    "realized_volatility",
    "average_volume",
    "volume_ratio",
    "volume_spike",
}

EXPECTED_SIGNAL_RULES = {"threshold", "crossover", "comparison"}
EXPECTED_AGGREGATORS = {"voting", "weighted_score", "logical_and", "logical_or"}
EXPECTED_PLACEHOLDERS = {
    "volatility_filter",
    "liquidity_filter",
    "regime_filter",
    "time_filter",
    "fixed_exit_rule",
    "trailing_exit_rule",
    "signal_based_exit_rule",
    "fixed_sizing",
    "volatility_adjusted_sizing",
    "confidence_weighted_sizing",
}


def test_all_existing_indicators_are_registered() -> None:
    registry = get_component_registry()

    names = {c.component_name for c in registry.list_components_by_type(ComponentType.INDICATOR)}

    assert names >= EXPECTED_INDICATORS


def test_indicator_metadata_has_inputs_warmup_and_parameters() -> None:
    registry = get_component_registry()

    for name in EXPECTED_INDICATORS:
        defn = registry.get_component_definition(name)
        assert defn.is_executable
        assert defn.implementation is not None
        assert defn.required_inputs
        assert defn.required_bar_fields
        assert defn.warmup_parameter or defn.warmup_bars is not None
        assert defn.warmup_formula
        assert defn.parameter_specs
        assert defn.deterministic


def test_all_existing_signal_rules_are_registered() -> None:
    registry = get_component_registry()

    names = {c.component_name for c in registry.list_components_by_type(ComponentType.SIGNAL_RULE)}

    assert names == EXPECTED_SIGNAL_RULES
    for name in EXPECTED_SIGNAL_RULES:
        defn = registry.get_component_definition(name)
        assert defn.is_executable
        assert defn.required_inputs
        assert defn.parameter_specs
        assert defn.output_type == "SignalRuleResult"


def test_all_existing_aggregators_are_registered() -> None:
    registry = get_component_registry()

    names = {c.component_name for c in registry.list_components_by_type(ComponentType.AGGREGATOR)}

    assert names == EXPECTED_AGGREGATORS
    for name in EXPECTED_AGGREGATORS:
        defn = registry.get_component_definition(name)
        assert defn.is_executable
        assert defn.required_inputs == ("SignalRuleResult",)
        assert defn.output_type == "SignalRuleResult"


def test_future_component_placeholders_are_non_executable() -> None:
    registry = get_component_registry()

    names = {c.component_name for c in registry.list_metadata_only_components()}

    assert names == EXPECTED_PLACEHOLDERS
    for name in EXPECTED_PLACEHOLDERS:
        defn = registry.get_component_definition(name)
        assert not defn.is_executable
        assert defn.metadata_only
        assert not defn.production_ready
        assert defn.experimental
        assert defn.implementation is None

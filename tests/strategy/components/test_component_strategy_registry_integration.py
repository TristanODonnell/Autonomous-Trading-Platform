from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.components import ComponentType, get_component_registry
from autonomous_trading_platform.strategy.registry import (
    StrategyDefinition,
    StrategyFamily,
    get_registry,
)
from autonomous_trading_platform.strategy.registry.strategy_registry import StrategyRegistry


def test_every_strategy_required_indicator_exists_in_component_registry() -> None:
    component_registry = get_component_registry()

    for defn in get_registry().list_definitions():
        for indicator_name in defn.required_indicators:
            component_registry.validate_component_reference(
                indicator_name,
                expected_type=ComponentType.INDICATOR,
            )


def test_debug_strategies_have_no_component_dependencies() -> None:
    for defn in get_registry().list_debug_strategies():
        assert defn.required_indicators == ()
        assert defn.required_persisted_features == ()


def test_strategy_registry_rejects_unknown_required_indicator() -> None:
    registry = StrategyRegistry()

    with pytest.raises(KeyError, match="Unknown component"):
        registry.register(
            StrategyDefinition(
                strategy_type="bad_indicator",
                display_name="Bad Indicator",
                description="Invalid dependency test.",
                family=StrategyFamily.DEBUG,
                implementation_class=object,
                debug=True,
                production_ready=False,
                default_parameters={},
                parameter_validator=lambda p: None,
                warmup_bars_fn=lambda p: 0,
                required_indicators=("not_registered",),
                required_persisted_features=(),
                parameter_specs=(),
                builder=lambda strategy_id, parameters: None,
            )
        )


def test_generation_candidates_exclude_metadata_only_components() -> None:
    candidates = get_component_registry().list_generation_candidates()

    assert candidates
    assert all(not c.metadata_only for c in candidates)

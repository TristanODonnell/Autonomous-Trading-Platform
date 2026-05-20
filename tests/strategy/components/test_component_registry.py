from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.components import (
    ComponentDefinition,
    ComponentRegistry,
    ComponentType,
    get_component_registry,
)


def test_component_registry_is_locked_after_import() -> None:
    assert get_component_registry().is_locked()


def test_component_registration_order_is_deterministic() -> None:
    registry = get_component_registry()

    assert registry.list_component_names() == registry.list_component_names()
    assert registry.list_component_names()[:3] == [
        "simple_moving_average",
        "exponential_moving_average",
        "momentum",
    ]


def test_fresh_registry_rejects_duplicate_registration() -> None:
    registry = ComponentRegistry()
    defn = ComponentDefinition(
        component_name="duplicate",
        component_type=ComponentType.INDICATOR,
        display_name="Duplicate",
        description="Duplicate test component.",
        implementation=lambda values: values,
        required_inputs=("values",),
    )

    registry.register(defn)

    with pytest.raises(ValueError, match="already registered"):
        registry.register(defn)


def test_registry_rejects_registration_after_lock() -> None:
    registry = ComponentRegistry()
    registry.lock()

    with pytest.raises(RuntimeError, match="locked"):
        registry.register(
            ComponentDefinition(
                component_name="late",
                component_type=ComponentType.INDICATOR,
                display_name="Late",
                description="Late test component.",
                implementation=lambda values: values,
                required_inputs=("values",),
            )
        )


def test_lookup_and_exists_api() -> None:
    registry = get_component_registry()

    assert registry.component_exists("momentum")
    assert registry.get_component_definition("momentum").display_name == "Momentum"
    assert not registry.component_exists("not_real")

    with pytest.raises(KeyError, match="Unknown component"):
        registry.get_component_definition("not_real")


def test_list_by_type_and_classification_api() -> None:
    registry = get_component_registry()

    indicators = registry.list_components_by_type(ComponentType.INDICATOR)
    placeholders = registry.list_metadata_only_components()
    executable = registry.list_executable_components()

    assert {c.component_name for c in indicators} >= {"simple_moving_average", "volume_ratio"}
    assert all(c.is_executable for c in executable)
    assert all(c.metadata_only for c in placeholders)
    assert not ({c.component_name for c in executable} & {c.component_name for c in placeholders})


def test_generation_candidates_exclude_metadata_only_by_default() -> None:
    registry = get_component_registry()

    candidates = registry.list_generation_candidates()

    assert candidates
    assert all(not c.metadata_only for c in candidates)


def test_validate_component_reference_checks_expected_type() -> None:
    registry = get_component_registry()

    registry.validate_component_reference("momentum", expected_type=ComponentType.INDICATOR)

    with pytest.raises(ValueError, match="expected 'signal_rule'"):
        registry.validate_component_reference("momentum", expected_type=ComponentType.SIGNAL_RULE)

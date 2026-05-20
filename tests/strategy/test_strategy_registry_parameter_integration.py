from __future__ import annotations

import inspect
from typing import Any

import pytest

from autonomous_trading_platform.strategy.registry import ParameterType, get_registry


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_every_registered_strategy_has_parameter_schema(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.parameter_schema is get_registry().get_parameter_schema(strategy_type)


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_registry_defaults_come_from_schema(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    assert defn.default_parameters == defn.normalize_parameters()
    assert get_registry().get_default_parameters(strategy_type) == defn.default_parameters


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_specs_match_schema_fields_and_defaults(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    schema_fields = set(defn.parameter_schema.model_fields)
    spec_defaults = {spec.name: spec.default for spec in defn.parameter_specs}

    assert set(defn.default_parameters) == schema_fields
    assert set(spec_defaults) == schema_fields
    assert spec_defaults == defn.default_parameters


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_parameter_spec_types_match_schema_defaults(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    for spec in defn.parameter_specs:
        default = defn.default_parameters[spec.name]
        if default is None:
            continue
        if spec.parameter_type == ParameterType.INT:
            assert isinstance(default, int) and not isinstance(default, bool)
        elif spec.parameter_type == ParameterType.FLOAT:
            assert isinstance(default, (int, float)) and not isinstance(default, bool)
        elif spec.parameter_type == ParameterType.BOOL:
            assert isinstance(default, bool)
        elif spec.parameter_type == ParameterType.STRING:
            assert isinstance(default, str)


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_schema_export_includes_registered_fields(strategy_type: str) -> None:
    exported = get_registry().export_parameter_schema(strategy_type)
    assert set(exported["properties"]) == set(
        get_registry().get_definition(strategy_type).default_parameters
    )


@pytest.mark.parametrize("strategy_type", get_registry().list_strategy_types())
def test_implementation_constructor_defaults_match_registry(strategy_type: str) -> None:
    defn = get_registry().get_definition(strategy_type)
    signature = inspect.signature(defn.implementation_class)
    constructor_defaults: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name == "strategy_id":
            continue
        if parameter.default is not inspect.Parameter.empty:
            constructor_defaults[name] = parameter.default

    assert constructor_defaults == defn.default_parameters

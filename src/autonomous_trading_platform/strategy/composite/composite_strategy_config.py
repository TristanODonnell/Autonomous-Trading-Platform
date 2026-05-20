from __future__ import annotations

import ast
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonomous_trading_platform.strategy.components import (
    ComponentParameterType,
    ComponentType,
    get_component_registry,
)


class CompositeInputReference(BaseModel):
    """Reference to an indicator output or a literal value."""

    model_config = ConfigDict(extra="forbid")

    indicator_id: str | None = None
    offset: int = 0
    value: float | bool | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> CompositeInputReference:
        has_indicator = self.indicator_id is not None
        has_value = self.value is not None
        if has_indicator == has_value:
            raise ValueError("input reference must set exactly one of indicator_id or value")
        if self.offset > 0:
            raise ValueError("offset cannot look ahead")
        return self


class CompositeIndicatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    component: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_is_stable(cls, value: str) -> str:
        if not value or not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "indicator id must be non-empty and contain only letters, numbers, _ or -"
            )
        return value


class CompositeRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    component: str
    inputs: dict[str, CompositeInputReference]
    parameters: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(default=1.0, gt=0.0)


class CompositeFilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    component: str
    input: CompositeInputReference
    operator: Literal["gt", "gte", "lt", "lte", "eq", "neq"]
    threshold: float | bool
    parameters: dict[str, Any] = Field(default_factory=dict)
    block_reason: str | None = None


class CompositeAggregatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str = "voting"
    parameters: dict[str, Any] = Field(default_factory=dict)


class CompositeConfidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["aggregation", "normalized", "weighted"] = "aggregation"
    floor: float = Field(default=0.0, ge=0.0, le=1.0)
    cap: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _floor_not_above_cap(self) -> CompositeConfidenceConfig:
        if self.floor > self.cap:
            raise ValueError("confidence floor cannot exceed cap")
        return self


def _default_indicators() -> list[CompositeIndicatorConfig]:
    return [
        CompositeIndicatorConfig(
            id="momentum_5",
            component="momentum",
            parameters={"lookback": 5},
        )
    ]


def _default_entry_rules() -> list[CompositeRuleConfig]:
    return [
        CompositeRuleConfig(
            id="momentum_threshold",
            component="threshold",
            inputs={"value": CompositeInputReference(indicator_id="momentum_5")},
            parameters={"buy_above": 0.0, "sell_below": 0.0, "confidence": 0.55},
            weight=1.0,
        )
    ]


def _default_aggregator() -> CompositeAggregatorConfig:
    return CompositeAggregatorConfig(component="voting", parameters={"min_votes": 1})


class CompositeStrategyConfig(BaseModel):
    """Declarative configuration for ``CompositeRuleStrategy``."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    strategy_type: Literal["composite_rule"] = "composite_rule"
    indicators: list[CompositeIndicatorConfig] = Field(default_factory=_default_indicators)
    entry_rules: list[CompositeRuleConfig] = Field(default_factory=_default_entry_rules)
    filters: list[CompositeFilterConfig] = Field(default_factory=list)
    confirmations: list[CompositeRuleConfig] = Field(default_factory=list)
    aggregator: CompositeAggregatorConfig = Field(default_factory=_default_aggregator)
    confidence_scoring: CompositeConfidenceConfig = Field(default_factory=CompositeConfidenceConfig)
    sizing: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_components(self) -> CompositeStrategyConfig:
        registry = get_component_registry()
        indicator_ids = [indicator.id for indicator in self.indicators]
        if len(indicator_ids) != len(set(indicator_ids)):
            raise ValueError("indicator ids must be unique within a composite config")
        if not self.entry_rules:
            raise ValueError("at least one entry rule is required")

        for indicator in self.indicators:
            registry.validate_component_reference(
                indicator.component, expected_type=ComponentType.INDICATOR
            )
            defn = registry.get_component_definition(indicator.component)
            if not defn.is_executable:
                raise ValueError(f"indicator component {indicator.component!r} is not executable")
            _validate_parameter_structure(indicator.component, indicator.parameters)

        for rule in [*self.entry_rules, *self.confirmations]:
            registry.validate_component_reference(
                rule.component, expected_type=ComponentType.SIGNAL_RULE
            )
            defn = registry.get_component_definition(rule.component)
            if not defn.is_executable:
                raise ValueError(f"signal rule component {rule.component!r} is not executable")
            _validate_required_inputs(rule.component, rule.inputs)
            _validate_input_references(rule.id, rule.inputs, set(indicator_ids))
            _validate_parameter_structure(rule.component, rule.parameters)

        for filter_config in self.filters:
            registry.validate_component_reference(
                filter_config.component,
                expected_type=ComponentType.FILTER,
            )
            _validate_input_references(
                filter_config.id,
                {"input": filter_config.input},
                set(indicator_ids),
            )

        registry.validate_component_reference(
            self.aggregator.component, expected_type=ComponentType.AGGREGATOR
        )
        aggregator_defn = registry.get_component_definition(self.aggregator.component)
        if not aggregator_defn.is_executable:
            raise ValueError(
                f"aggregator component {self.aggregator.component!r} is not executable"
            )
        _validate_parameter_structure(self.aggregator.component, self.aggregator.parameters)

        return self

    def canonical_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.model_dump(mode="json", exclude_none=True))

    def required_indicator_components(self) -> tuple[str, ...]:
        seen: list[str] = []
        for indicator in self.indicators:
            if indicator.component not in seen:
                seen.append(indicator.component)
        return tuple(seen)

    def warmup_bars(self) -> int:
        registry = get_component_registry()
        max_warmup = 0
        max_offsets_by_indicator = self._max_offsets_by_indicator()
        for indicator in self.indicators:
            defn = registry.get_component_definition(indicator.component)
            base_warmup = _component_warmup(defn.component_name, indicator.parameters)
            max_warmup = max(
                max_warmup, base_warmup + max_offsets_by_indicator.get(indicator.id, 0)
            )

        for rule in [*self.entry_rules, *self.confirmations]:
            rule_defn = registry.get_component_definition(rule.component)
            if rule_defn.warmup_bars is not None:
                max_warmup = max(max_warmup, rule_defn.warmup_bars)

        return max_warmup

    def _max_offsets_by_indicator(self) -> dict[str, int]:
        offsets: dict[str, int] = {}
        for ref in self._all_input_references():
            if ref.indicator_id is None:
                continue
            offsets[ref.indicator_id] = max(offsets.get(ref.indicator_id, 0), abs(ref.offset))
        return offsets

    def _all_input_references(self) -> list[CompositeInputReference]:
        refs: list[CompositeInputReference] = []
        for rule in [*self.entry_rules, *self.confirmations]:
            refs.extend(rule.inputs.values())
        refs.extend(filter_config.input for filter_config in self.filters)
        return refs


def _validate_required_inputs(
    component_name: str, inputs: dict[str, CompositeInputReference]
) -> None:
    registry = get_component_registry()
    required = registry.get_required_inputs(component_name)
    missing = [name for name in required if name not in inputs]
    if missing:
        raise ValueError(f"component {component_name!r} is missing required inputs: {missing}")


def _validate_input_references(
    owner_id: str,
    inputs: dict[str, CompositeInputReference],
    indicator_ids: set[str],
) -> None:
    for input_name, ref in inputs.items():
        if ref.indicator_id is not None and ref.indicator_id not in indicator_ids:
            raise ValueError(
                f"{owner_id!r} input {input_name!r} references unknown indicator id "
                f"{ref.indicator_id!r}"
            )


def _validate_parameter_structure(component_name: str, parameters: dict[str, Any]) -> None:
    registry = get_component_registry()
    specs = {spec.name: spec for spec in registry.get_component_parameter_specs(component_name)}
    if component_name == "comparison":
        specs = {
            **specs,
            "buy_when_left_above": _AdHocSpec(ComponentParameterType.BOOL, None, None),
            "sell_when_left_below": _AdHocSpec(ComponentParameterType.BOOL, None, None),
        }
    if component_name in {"logical_and", "logical_or"}:
        allowed = {"target_direction", "direction"}
        unknown = sorted(set(parameters) - allowed)
        if unknown:
            raise ValueError(f"component {component_name!r} received unknown parameters: {unknown}")
        for name, value in parameters.items():
            if value not in {"buy", "sell"}:
                raise ValueError(f"{component_name}.{name} must be 'buy' or 'sell'")
        return

    unknown = sorted(set(parameters) - set(specs))
    if unknown:
        raise ValueError(f"component {component_name!r} received unknown parameters: {unknown}")

    for name, spec in specs.items():
        if name not in parameters:
            continue
        value = parameters[name]
        if spec.parameter_type == ComponentParameterType.INT:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{component_name}.{name} must be an integer")
            numeric_value = float(value)
        elif spec.parameter_type == ComponentParameterType.FLOAT:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{component_name}.{name} must be a number")
            numeric_value = float(value)
        elif spec.parameter_type == ComponentParameterType.BOOL:
            if not isinstance(value, bool):
                raise ValueError(f"{component_name}.{name} must be a boolean")
            continue
        else:
            if not isinstance(value, str):
                raise ValueError(f"{component_name}.{name} must be a string")
            continue

        if spec.min_value is not None and numeric_value < spec.min_value:
            raise ValueError(f"{component_name}.{name} must be >= {spec.min_value}")
        if spec.max_value is not None and numeric_value > spec.max_value:
            raise ValueError(f"{component_name}.{name} must be <= {spec.max_value}")


class _AdHocSpec:
    def __init__(
        self,
        parameter_type: ComponentParameterType,
        min_value: float | None,
        max_value: float | None,
    ) -> None:
        self.parameter_type = parameter_type
        self.min_value = min_value
        self.max_value = max_value


def _component_warmup(component_name: str, parameters: dict[str, Any]) -> int:
    registry = get_component_registry()
    defn = registry.get_component_definition(component_name)
    if defn.warmup_formula:
        defaults = {spec.name: spec.default for spec in defn.parameter_specs}
        values = {**defaults, **parameters}
        return int(_evaluate_warmup_formula(defn.warmup_formula, values))
    if defn.warmup_parameter:
        defaults = {spec.name: spec.default for spec in defn.parameter_specs}
        values = {**defaults, **parameters}
        return int(values[defn.warmup_parameter])
    return int(defn.warmup_bars or 0)


def _evaluate_warmup_formula(formula: str, values: dict[str, Any]) -> float:
    node = ast.parse(formula, mode="eval")
    return float(_eval_ast_node(node.body, values))


def _eval_ast_node(node: ast.AST, values: dict[str, Any]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError(f"unknown warmup parameter {node.id!r}")
        value = values[node.id]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"warmup parameter {node.id!r} must be numeric")
        return float(value)
    if isinstance(node, ast.BinOp):
        left = _eval_ast_node(node.left, values)
        right = _eval_ast_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
    raise ValueError("unsupported warmup formula")

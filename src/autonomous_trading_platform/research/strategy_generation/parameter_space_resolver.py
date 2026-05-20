from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from autonomous_trading_platform.strategy.registry import (
    ParameterSpec,
    ParameterType,
    StrategyRegistry,
    get_registry,
)


class ParameterSpaceResolver:
    """Derive deterministic, schema-aware parameter value lists from StrategyRegistry."""

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.registry = registry or get_registry()

    def resolve(
        self,
        strategy_type: str,
        overrides: Mapping[str, Iterable[Any]] | None = None,
    ) -> dict[str, list[Any]]:
        defn = self.registry.get_definition(strategy_type)
        specs = {spec.name: spec for spec in defn.parameter_specs}
        overrides = overrides or {}
        unknown = sorted(set(overrides) - set(specs))
        if unknown:
            raise ValueError(f"Unknown parameter names for {strategy_type!r}: {unknown}")

        resolved: dict[str, list[Any]] = {}
        for name in sorted(specs):
            spec = specs[name]
            if not spec.tunable and name not in overrides:
                continue
            values = list(overrides[name]) if name in overrides else self._values_for_spec(spec)
            if not values:
                continue
            normalized_values = self._normalize_values(spec, values)
            self._validate_values(spec, normalized_values)
            if normalized_values:
                resolved[name] = normalized_values
        return resolved

    def _values_for_spec(self, spec: ParameterSpec) -> list[Any]:
        if spec.parameter_type == ParameterType.BOOL:
            return [False, True]
        if spec.parameter_type == ParameterType.STRING:
            return [spec.default] if spec.default is not None else []
        if spec.min_value is None or spec.max_value is None:
            return [spec.default] if spec.default is not None else []
        if spec.parameter_type == ParameterType.INT:
            int_low = int(spec.min_value)
            int_high = int(spec.max_value)
            step = max(int(spec.step or 1), 1)
            if spec.discrete and int_high - int_low <= 20:
                values = list(range(int_low, int_high + 1, step))
            else:
                values = sorted({int_low, int(spec.default), int_high})
            return values
        if spec.parameter_type == ParameterType.FLOAT:
            float_low = float(spec.min_value)
            float_high = float(spec.max_value)
            if spec.discrete and spec.step:
                float_values: list[float] = []
                current = float_low
                while current <= float_high + 1e-12 and len(float_values) < 100:
                    float_values.append(round(current, 10))
                    current += float(spec.step)
                return float_values
            mid = (
                float(spec.default) if spec.default is not None else (float_low + float_high) / 2.0
            )
            return sorted({round(float_low, 10), round(mid, 10), round(float_high, 10)})
        return [spec.default] if spec.default is not None else []

    def _normalize_values(self, spec: ParameterSpec, values: list[Any]) -> list[Any]:
        normalized: list[Any] = []
        seen: set[Any] = set()
        for value in values:
            parsed = self._coerce_value(spec, value)
            key = parsed
            if key in seen:
                continue
            seen.add(key)
            normalized.append(parsed)
        return normalized

    def _coerce_value(self, spec: ParameterSpec, value: Any) -> Any:
        if spec.parameter_type == ParameterType.INT:
            if isinstance(value, bool):
                raise ValueError(f"{spec.name} must be an integer")
            return int(value)
        if spec.parameter_type == ParameterType.FLOAT:
            if isinstance(value, bool):
                raise ValueError(f"{spec.name} must be a number")
            return float(value)
        if spec.parameter_type == ParameterType.BOOL:
            if not isinstance(value, bool):
                raise ValueError(f"{spec.name} must be a boolean")
            return value
        if spec.parameter_type == ParameterType.STRING:
            if not isinstance(value, str):
                raise ValueError(f"{spec.name} must be a string")
            return value
        return value

    def _validate_values(self, spec: ParameterSpec, values: list[Any]) -> None:
        for value in values:
            if spec.parameter_type in {ParameterType.INT, ParameterType.FLOAT}:
                numeric = float(value)
                if spec.min_value is not None and numeric < spec.min_value:
                    raise ValueError(f"{spec.name} must be >= {spec.min_value}")
                if spec.max_value is not None and numeric > spec.max_value:
                    raise ValueError(f"{spec.name} must be <= {spec.max_value}")

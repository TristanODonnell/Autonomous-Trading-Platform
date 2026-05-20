"""Canonical metadata model for registered strategy components."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .component_parameter_schemas import ComponentParameterSpec
from .component_type import ComponentType


@dataclass(frozen=True)
class ComponentDefinition:
    """Metadata record for one reusable strategy-construction primitive."""

    component_name: str
    component_type: ComponentType
    display_name: str
    description: str
    implementation: Callable[..., Any] | type | None = None
    is_executable: bool = True
    metadata_only: bool = False

    required_inputs: tuple[str, ...] = ()
    input_types: Mapping[str, str] = field(default_factory=dict)
    required_price_basis: str | None = None
    required_bar_fields: tuple[str, ...] = ()

    parameter_specs: tuple[ComponentParameterSpec, ...] = ()
    constraints: tuple[str, ...] = ()
    optimization_ranges: Mapping[str, tuple[float | int, float | int]] = field(default_factory=dict)
    mutation_metadata: Mapping[str, str] = field(default_factory=dict)

    warmup_bars: int | None = None
    warmup_parameter: str | None = None
    warmup_formula: str | None = None
    deterministic: bool = True
    output_type: str = "float"
    output_domain: str | None = None

    compatible_component_types: tuple[ComponentType, ...] = ()
    incompatible_components: tuple[str, ...] = ()
    allowed_strategy_families: tuple[str, ...] = ()
    supports_intraday: bool = True
    supports_daily: bool = True
    supports_raw_prices: bool = True
    supports_adjusted_prices: bool = True

    production_ready: bool = True
    debug: bool = False
    experimental: bool = False

    def __post_init__(self) -> None:
        if self.metadata_only and self.is_executable:
            raise ValueError("metadata-only components cannot be executable")
        if self.is_executable and self.implementation is None:
            raise ValueError("executable components require an implementation reference")
        if not self.component_name:
            raise ValueError("component_name is required")
        if not self.display_name:
            raise ValueError("display_name is required")

        object.__setattr__(self, "component_type", ComponentType(self.component_type))
        object.__setattr__(self, "required_inputs", tuple(self.required_inputs))
        object.__setattr__(self, "required_bar_fields", tuple(self.required_bar_fields))
        object.__setattr__(self, "parameter_specs", tuple(self.parameter_specs))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(
            self,
            "compatible_component_types",
            tuple(ComponentType(t) for t in self.compatible_component_types),
        )
        object.__setattr__(self, "incompatible_components", tuple(self.incompatible_components))
        object.__setattr__(self, "allowed_strategy_families", tuple(self.allowed_strategy_families))
        object.__setattr__(self, "input_types", MappingProxyType(dict(self.input_types)))
        object.__setattr__(
            self,
            "optimization_ranges",
            MappingProxyType(dict(self.optimization_ranges)),
        )
        object.__setattr__(
            self,
            "mutation_metadata",
            MappingProxyType(dict(self.mutation_metadata)),
        )

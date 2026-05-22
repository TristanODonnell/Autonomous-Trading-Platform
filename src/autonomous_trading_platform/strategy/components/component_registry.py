"""Deterministic registry for reusable strategy components."""

from __future__ import annotations

from .component_definition import ComponentDefinition
from .component_type import ComponentType


class ComponentRegistry:
    """Order-preserving component registry, immutable after ``lock()``."""

    def __init__(self) -> None:
        self._definitions: dict[str, ComponentDefinition] = {}
        self._order: list[str] = []
        self._locked = False

    def register(self, defn: ComponentDefinition) -> None:
        if self._locked:
            raise RuntimeError(
                f"ComponentRegistry is locked; cannot register {defn.component_name!r}"
            )
        if defn.component_name in self._definitions:
            raise ValueError(f"Component {defn.component_name!r} is already registered")
        self._definitions[defn.component_name] = defn
        self._order.append(defn.component_name)

    def lock(self) -> None:
        self._locked = True

    def is_locked(self) -> bool:
        return self._locked

    def list_components(self) -> list[ComponentDefinition]:
        return [self._definitions[name] for name in self._order]

    def list_component_names(self) -> list[str]:
        return list(self._order)

    def list_components_by_type(
        self,
        component_type: ComponentType | str,
    ) -> list[ComponentDefinition]:
        normalized_type = ComponentType(component_type)
        return [
            self._definitions[name]
            for name in self._order
            if self._definitions[name].component_type == normalized_type
        ]

    def get_component_definition(self, component_name: str) -> ComponentDefinition:
        try:
            return self._definitions[component_name]
        except KeyError:
            raise KeyError(f"Unknown component: {component_name!r}") from None

    def component_exists(self, component_name: str) -> bool:
        return component_name in self._definitions

    def list_executable_components(self) -> list[ComponentDefinition]:
        return [
            self._definitions[name] for name in self._order if self._definitions[name].is_executable
        ]

    def list_metadata_only_components(self) -> list[ComponentDefinition]:
        return [
            self._definitions[name] for name in self._order if self._definitions[name].metadata_only
        ]

    def list_generation_candidates(
        self,
        *,
        include_metadata_only: bool = False,
    ) -> list[ComponentDefinition]:
        return [
            self._definitions[name]
            for name in self._order
            if self._definitions[name].parameter_specs
            and (include_metadata_only or not self._definitions[name].metadata_only)
        ]

    def validate_component_reference(
        self,
        component_name: str,
        *,
        expected_type: ComponentType | str | None = None,
    ) -> None:
        defn = self.get_component_definition(component_name)
        if expected_type is not None and defn.component_type != ComponentType(expected_type):
            raise ValueError(
                f"Component {component_name!r} has type {defn.component_type.value!r}; "
                f"expected {ComponentType(expected_type).value!r}"
            )

    def list_compatible_components(self, component_name: str) -> list[ComponentDefinition]:
        source = self.get_component_definition(component_name)
        if not source.compatible_component_types:
            return []
        return [
            self._definitions[name]
            for name in self._order
            if self._definitions[name].component_type in source.compatible_component_types
            and name not in source.incompatible_components
        ]

    def get_required_inputs(self, component_name: str) -> tuple[str, ...]:
        return self.get_component_definition(component_name).required_inputs

    def get_component_parameter_specs(self, component_name: str):
        return self.get_component_definition(component_name).parameter_specs


_COMPONENT_REGISTRY = ComponentRegistry()


def get_component_registry() -> ComponentRegistry:
    return _COMPONENT_REGISTRY

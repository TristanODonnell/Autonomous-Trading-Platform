"""Canonical ComponentRegistry for reusable strategy primitives."""

from __future__ import annotations

from . import _registrations as _registrations  # noqa: F401
from .component_definition import ComponentDefinition
from .component_parameter_schemas import ComponentParameterSpec, ComponentParameterType
from .component_registry import ComponentRegistry, get_component_registry
from .component_type import ComponentType

__all__ = [
    "ComponentDefinition",
    "ComponentParameterSpec",
    "ComponentParameterType",
    "ComponentRegistry",
    "ComponentType",
    "get_component_registry",
]

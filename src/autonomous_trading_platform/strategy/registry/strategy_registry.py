"""StrategyRegistry: centralized, deterministic, immutable-after-lock strategy catalog.

Usage
-----
    from autonomous_trading_platform.strategy.registry import get_registry

    defn = get_registry().get_definition("momentum")
    types = get_registry().list_strategy_types()
"""

from __future__ import annotations

from typing import Any

from .parameter_schemas import StrategyParameterSchema
from .strategy_definition import StrategyDefinition
from .strategy_family import StrategyFamily


class StrategyRegistry:
    """Deterministic registry of all strategy definitions.

    Registration is order-preserving and open until ``lock()`` is called.
    After locking, further ``register()`` calls raise ``RuntimeError``.
    All query methods return results in original registration order.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, StrategyDefinition] = {}
        self._order: list[str] = []
        self._locked = False

    # ------------------------------------------------------------------
    # Registration lifecycle
    # ------------------------------------------------------------------

    def register(self, defn: StrategyDefinition) -> None:
        """Register a strategy definition.

        Raises ``RuntimeError`` if the registry is locked.
        Raises ``ValueError`` if the strategy type is already registered.
        """
        if self._locked:
            raise RuntimeError(
                f"StrategyRegistry is locked; cannot register {defn.strategy_type!r}"
            )
        if defn.strategy_type in self._definitions:
            raise ValueError(f"Strategy type {defn.strategy_type!r} is already registered")
        from autonomous_trading_platform.strategy.components import (
            ComponentType,
            get_component_registry,
        )

        component_registry = get_component_registry()
        for indicator_name in defn.required_indicators:
            component_registry.validate_component_reference(
                indicator_name,
                expected_type=ComponentType.INDICATOR,
            )
        self._definitions[defn.strategy_type] = defn
        self._order.append(defn.strategy_type)

    def lock(self) -> None:
        """Seal the registry against further registrations."""
        self._locked = True

    def is_locked(self) -> bool:
        return self._locked

    # ------------------------------------------------------------------
    # Required lookup API
    # ------------------------------------------------------------------

    def get_definition(self, strategy_type: str) -> StrategyDefinition:
        """Return the definition for *strategy_type*.

        Raises ``KeyError`` if the type is not registered.
        """
        try:
            return self._definitions[strategy_type]
        except KeyError:
            raise KeyError(f"Unknown strategy type: {strategy_type!r}") from None

    def get_parameter_schema(self, strategy_type: str) -> type[StrategyParameterSchema]:
        return self.get_definition(strategy_type).parameter_schema

    def get_default_parameters(self, strategy_type: str) -> dict[str, Any]:
        return self.get_definition(strategy_type).normalize_parameters()

    def validate_parameters(self, strategy_type: str, parameters: dict[str, Any] | None) -> None:
        self.get_definition(strategy_type).validate_parameters(parameters)

    def normalize_parameters(
        self, strategy_type: str, parameters: dict[str, Any] | None
    ) -> dict[str, Any]:
        return self.get_definition(strategy_type).normalize_parameters(parameters)

    def export_parameter_schema(self, strategy_type: str) -> dict[str, Any]:
        return self.get_definition(strategy_type).export_parameter_schema()

    def strategy_exists(self, strategy_type: str) -> bool:
        return strategy_type in self._definitions

    def list_definitions(self) -> list[StrategyDefinition]:
        """All definitions in registration order."""
        return [self._definitions[t] for t in self._order]

    def list_strategy_types(self) -> list[str]:
        """All registered type strings in registration order."""
        return list(self._order)

    def list_families(self) -> list[StrategyFamily]:
        """Unique families present in the registry, in first-seen order."""
        seen: list[StrategyFamily] = []
        for t in self._order:
            f = self._definitions[t].family
            if f not in seen:
                seen.append(f)
        return seen

    def list_debug_strategies(self) -> list[StrategyDefinition]:
        return [self._definitions[t] for t in self._order if self._definitions[t].debug]

    def list_production_strategies(self) -> list[StrategyDefinition]:
        return [self._definitions[t] for t in self._order if not self._definitions[t].debug]

    # ------------------------------------------------------------------
    # Optional lookup API
    # ------------------------------------------------------------------

    def get_family_strategies(self, family: StrategyFamily) -> list[StrategyDefinition]:
        return [self._definitions[t] for t in self._order if self._definitions[t].family == family]

    def get_generation_candidates(self) -> list[StrategyDefinition]:
        """Strategies with non-empty parameter_specs; suitable for search-space generation."""
        return [self._definitions[t] for t in self._order if self._definitions[t].parameter_specs]


# ---------------------------------------------------------------------------
# Module-level singleton — populated by _registrations on first import of
# the registry package (__init__.py imports _registrations).
# ---------------------------------------------------------------------------

_REGISTRY = StrategyRegistry()


def get_registry() -> StrategyRegistry:
    """Return the canonical StrategyRegistry singleton."""
    return _REGISTRY

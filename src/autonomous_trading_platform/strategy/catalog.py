"""Backward-compatible shim for strategy catalog helpers.

All canonical metadata now lives in ``strategy.registry``.  This module
re-exports the public helpers under their original names so that existing
imports continue to work without modification.

New code should import directly from ``strategy.registry``.
"""

from __future__ import annotations

from autonomous_trading_platform.strategy.registry import StrategyDefinition, get_registry

# StrategyCatalogEntry is now StrategyDefinition.  The alias preserves
# isinstance() checks and attribute access from existing call sites.
StrategyCatalogEntry = StrategyDefinition


# ---------------------------------------------------------------------------
# Public helpers (unchanged signatures)
# ---------------------------------------------------------------------------


def list_strategy_types() -> list[str]:
    """All registered strategy type strings."""
    return get_registry().list_strategy_types()


def list_production_strategy_types() -> list[str]:
    """Strategy types that are not flagged as debug."""
    return [d.strategy_type for d in get_registry().list_production_strategies()]


def list_debug_strategy_types() -> list[str]:
    """Strategy types flagged as debug/test-only."""
    return [d.strategy_type for d in get_registry().list_debug_strategies()]


def get_strategy_definition(strategy_type: str) -> StrategyDefinition:
    """Return the registry entry for *strategy_type*.

    Raises ``KeyError`` if the type is not registered.
    """
    return get_registry().get_definition(strategy_type)


def strategy_type_exists(strategy_type: str) -> bool:
    """True if *strategy_type* is present in the registry."""
    return get_registry().strategy_exists(strategy_type)

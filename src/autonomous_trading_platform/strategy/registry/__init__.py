"""strategy.registry — canonical StrategyRegistry and StrategyDefinition.

Importing this package triggers all strategy registrations and locks the
registry.  Use ``get_registry()`` to access the singleton.

Public API
----------
    get_registry()          -> StrategyRegistry
    StrategyDefinition      (metadata model)
    StrategyFamily          (family enum)
    ParameterSpec           (per-parameter search-space spec)
    ParameterType           (INT | FLOAT | BOOL | STRING)
"""

from __future__ import annotations

# Populate and lock the registry on first import.
from . import _registrations as _registrations  # noqa: F401
from .parameter_metadata import ParameterSpec, ParameterType
from .parameter_schemas import (
    FactorBasedParameters,
    IntentionalLoserParameters,
    MeanReversionParameters,
    MomentumParameters,
    MovingAverageCrossoverParameters,
    RandomParameters,
    StrategyParameterSchema,
    StubParameters,
)
from .strategy_definition import StrategyDefinition
from .strategy_family import StrategyFamily
from .strategy_registry import StrategyRegistry, get_registry

__all__ = [
    "get_registry",
    "StrategyDefinition",
    "StrategyFamily",
    "StrategyRegistry",
    "ParameterSpec",
    "ParameterType",
    "StrategyParameterSchema",
    "MovingAverageCrossoverParameters",
    "MomentumParameters",
    "MeanReversionParameters",
    "FactorBasedParameters",
    "RandomParameters",
    "StubParameters",
    "IntentionalLoserParameters",
]

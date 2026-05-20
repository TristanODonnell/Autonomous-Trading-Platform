"""Per-strategy parameter validation.

Implementations now live in ``strategy.registry.validators``.
This module re-exports everything for backward compatibility.
"""

from __future__ import annotations

from autonomous_trading_platform.strategy.registry.validators import (
    VALIDATORS as _VALIDATORS,
)
from autonomous_trading_platform.strategy.registry.validators import (
    _noop,
    _require_float_range,
    _require_non_negative_float,
    _require_positive_int,
    _validate_factor_based,
    _validate_intentional_loser,
    _validate_mean_reversion,
    _validate_momentum,
    _validate_moving_average_crossover,
    _validate_random,
    _validate_stub,
    validate_strategy_parameters,
)

__all__ = [
    "validate_strategy_parameters",
    "_validate_moving_average_crossover",
    "_validate_momentum",
    "_validate_mean_reversion",
    "_validate_factor_based",
    "_validate_random",
    "_validate_intentional_loser",
    "_validate_stub",
    "_require_positive_int",
    "_require_float_range",
    "_require_non_negative_float",
    "_noop",
    "_VALIDATORS",
]

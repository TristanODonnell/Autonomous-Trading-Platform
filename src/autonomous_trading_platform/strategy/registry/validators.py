"""Per-strategy parameter validation functions.

These are pure functions that validate a parameters dict for one strategy type.
They raise ``ValueError`` with a clear, field-specific message on failure.

The dispatch table ``VALIDATORS`` maps strategy_type -> validator callable.
``validate_strategy_parameters()`` is the main entry point.
"""

from __future__ import annotations

from typing import Any

from .parameter_schemas import SCHEMAS_BY_STRATEGY


def validate_strategy_parameters(strategy_type: str, parameters: dict[str, Any]) -> None:
    """Validate *parameters* for *strategy_type*.

    Raises ``ValueError`` if any parameter is out of range, the wrong type,
    or an invalid combination exists.  Does nothing for unknown strategy
    types — registry existence checking is handled separately.
    """
    schema = SCHEMAS_BY_STRATEGY.get(strategy_type)
    if schema is None:
        _noop(parameters)
        return
    schema.model_validate(parameters)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_positive_int(params: dict, key: str) -> None:
    v = params.get(key)
    if v is None:
        return
    if not isinstance(v, int) or isinstance(v, bool):
        raise ValueError(f"{key} must be an integer, got {type(v).__name__}")
    if v <= 0:
        raise ValueError(f"{key} must be > 0, got {v}")


def _require_float_range(params: dict, key: str, lo: float, hi: float) -> None:
    v = params.get(key)
    if v is None:
        return
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError(f"{key} must be a number, got {type(v).__name__}")
    if not (lo <= float(v) <= hi):
        raise ValueError(f"{key} must be in [{lo}, {hi}], got {v}")


def _require_non_negative_float(params: dict, key: str) -> None:
    v = params.get(key)
    if v is None:
        return
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise ValueError(f"{key} must be a number, got {type(v).__name__}")
    if float(v) < 0:
        raise ValueError(f"{key} must be >= 0, got {v}")


def _noop(_params: dict) -> None:
    pass


# ---------------------------------------------------------------------------
# Per-strategy validators
# ---------------------------------------------------------------------------


def _validate_moving_average_crossover(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["moving_average_crossover"].model_validate(params)


def _validate_momentum(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["momentum"].model_validate(params)


def _validate_mean_reversion(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["mean_reversion"].model_validate(params)


def _validate_factor_based(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["factor_based"].model_validate(params)


def _validate_random(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["random"].model_validate(params)


def _validate_intentional_loser(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["intentional_loser"].model_validate(params)


def _validate_stub(params: dict[str, Any]) -> None:
    SCHEMAS_BY_STRATEGY["stub"].model_validate(params)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

VALIDATORS: dict[str, Any] = {
    "moving_average_crossover": _validate_moving_average_crossover,
    "momentum": _validate_momentum,
    "mean_reversion": _validate_mean_reversion,
    "factor_based": _validate_factor_based,
    "random": _validate_random,
    "intentional_loser": _validate_intentional_loser,
    "stub": _validate_stub,
}

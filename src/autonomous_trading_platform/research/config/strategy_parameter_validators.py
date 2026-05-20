"""Per-strategy parameter validation.

Each strategy type has specific parameter constraints that are validated here.
Unknown strategy types are skipped (catalog validation handles existence checks).

Raises ValueError with a clear, field-specific message on failure.
"""

from __future__ import annotations

from typing import Any


def validate_strategy_parameters(strategy_type: str, parameters: dict[str, Any]) -> None:
    """Validate parameters for the given strategy type.

    Raises ValueError if any parameter is out of range, the wrong type,
    or an invalid combination exists (e.g. short_window >= long_window).
    Does nothing for unknown strategy types — catalog validation handles
    existence checking separately.
    """
    _VALIDATORS.get(strategy_type, _noop)(parameters)


# ---------------------------------------------------------------------------
# Helpers
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
    _require_positive_int(params, "short_window")
    _require_positive_int(params, "long_window")

    short = params.get("short_window")
    long_ = params.get("long_window")
    if short is not None and long_ is not None:
        if not isinstance(short, int) or not isinstance(long_, int):
            return  # type errors reported above
        if short >= long_:
            raise ValueError(
                f"short_window ({short}) must be strictly less than long_window ({long_})"
            )


def _validate_momentum(params: dict[str, Any]) -> None:
    _require_positive_int(params, "lookback")


def _validate_mean_reversion(params: dict[str, Any]) -> None:
    _require_positive_int(params, "window")

    buy_z = params.get("buy_below_z")
    sell_z = params.get("sell_above_z")
    if buy_z is not None and sell_z is not None:
        if not isinstance(buy_z, (int, float)) or not isinstance(sell_z, (int, float)):
            return
        if float(buy_z) >= float(sell_z):
            raise ValueError(
                f"buy_below_z ({buy_z}) must be strictly less than sell_above_z ({sell_z})"
            )


def _validate_factor_based(params: dict[str, Any]) -> None:
    for key in ("momentum_lookback", "mean_reversion_window", "volatility_window", "volume_window"):
        _require_positive_int(params, key)

    for key in ("momentum_weight", "mean_reversion_weight", "volume_weight", "volatility_weight"):
        _require_non_negative_float(params, key)

    buy_thresh = params.get("buy_score_threshold")
    sell_thresh = params.get("sell_score_threshold")
    if buy_thresh is not None and sell_thresh is not None:
        if not isinstance(buy_thresh, (int, float)) or not isinstance(sell_thresh, (int, float)):
            return
        if float(sell_thresh) >= float(buy_thresh):
            raise ValueError(
                f"sell_score_threshold ({sell_thresh}) must be strictly less than "
                f"buy_score_threshold ({buy_thresh})"
            )


def _validate_random(params: dict[str, Any]) -> None:
    _require_float_range(params, "signal_probability", 0.0, 1.0)
    _require_float_range(params, "buy_probability", 0.0, 1.0)


def _validate_intentional_loser(params: dict[str, Any]) -> None:
    _require_non_negative_float(params, "price_change_threshold")


def _validate_stub(params: dict[str, Any]) -> None:
    _require_non_negative_float(params, "price_change_threshold")


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_VALIDATORS: dict[str, Any] = {
    "moving_average_crossover": _validate_moving_average_crossover,
    "momentum": _validate_momentum,
    "mean_reversion": _validate_mean_reversion,
    "factor_based": _validate_factor_based,
    "random": _validate_random,
    "intentional_loser": _validate_intentional_loser,
    "stub": _validate_stub,
}

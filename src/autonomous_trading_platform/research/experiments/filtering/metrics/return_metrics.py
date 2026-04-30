"""

Computes total return and CAGR from a simulation equity curve.

equity_curve columns: timestamp, equity, cash, positions_value, drawdown
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


@dataclass(frozen=True, slots=True)
class ReturnMetrics:
    total_return: float  # (final - initial) / initial
    cagr: float  # annualised: (final/initial)^(1/years) - 1
    initial_equity: float
    final_equity: float
    duration_days: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(equity_curve: pd.DataFrame) -> None:
    if equity_curve is None or equity_curve.empty:
        raise ValueError("equity_curve is empty.")
    for col in ("timestamp", "equity"):
        if col not in equity_curve.columns:
            raise ValueError(f"equity_curve missing column: '{col}'")


def _sorted(equity_curve: pd.DataFrame) -> pd.DataFrame:
    return equity_curve.sort_values("timestamp")


def _endpoints(equity_curve: pd.DataFrame) -> tuple[float, float]:
    s = _sorted(equity_curve)
    return float(s["equity"].iloc[0]), float(s["equity"].iloc[-1])


def _duration_days(equity_curve: pd.DataFrame) -> float:
    s = _sorted(equity_curve)
    delta: timedelta = (
        pd.Timestamp(s["timestamp"].iloc[-1]) - pd.Timestamp(s["timestamp"].iloc[0])
    ).to_pytimedelta()
    return delta.total_seconds() / 86_400.0


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def total_return(equity_curve: pd.DataFrame) -> float:
    """(final - initial) / initial. Raises if initial == 0."""
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    if initial == 0.0:
        raise ValueError("initial_equity is 0 — check initial_cash on SimulationRunRequest.")
    return (final - initial) / initial


def cagr(equity_curve: pd.DataFrame) -> float:
    """
    (final/initial)^(1/years) - 1.
    Returns 0.0 if window < 1 day; -1.0 if portfolio went to zero or negative.
    """
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    if initial == 0.0:
        raise ValueError("initial_equity is 0 — check initial_cash on SimulationRunRequest.")
    days = _duration_days(equity_curve)
    if days < 1.0:
        return 0.0
    ratio = final / initial
    if ratio <= 0.0:
        return -1.0
    return float(ratio ** (1.0 / (days / 365.0))) - 1.0


def return_metrics(equity_curve: pd.DataFrame) -> ReturnMetrics:
    """Compute all return metrics in one call."""
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    return ReturnMetrics(
        total_return=total_return(equity_curve),
        cagr=cagr(equity_curve),
        initial_equity=initial,
        final_equity=final,
        duration_days=_duration_days(equity_curve),
    )

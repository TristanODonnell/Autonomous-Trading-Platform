"""
Computes total return and CAGR from a simulation equity curve.

equity_curve columns: timestamp, equity, cash, positions_value, drawdown

Annualisation convention: trading days (see annualisation.py).
CAGR exponent = (bars - 1) / bars_per_year, matching the trading-day base
used by Sharpe/Sortino/volatility in risk_metrics.py.  Both files import
BARS_PER_YEAR from annualisation.py so the constant is never duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR


@dataclass(frozen=True, slots=True)
class ReturnMetrics:
    total_return: float  # (final - initial) / initial
    cagr: float  # annualised: (final/initial)^(bars_per_year/(bars-1)) - 1
    initial_equity: float
    final_equity: float
    duration_bars: int  # number of bars in the equity curve


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


def _duration_bars(equity_curve: pd.DataFrame) -> int:
    """Number of bars in the curve (not the number of periods between them)."""
    return len(equity_curve)


def total_return(equity_curve: pd.DataFrame) -> float:
    """(final - initial) / initial. Raises if initial == 0."""
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    if initial == 0.0:
        raise ValueError("initial_equity is 0 — check initial_cash on SimulationRunRequest.")
    return (final - initial) / initial


def cagr(
    equity_curve: pd.DataFrame,
    bars_per_year: int = BARS_PER_YEAR,
) -> float:
    """
    (final/initial)^(bars_per_year / (bars - 1)) - 1.

    Annualised on a trading-day basis, consistent with Sharpe/Sortino/volatility
    in risk_metrics.py.  Both use bars_per_year (default 19_656 = 252 * 78) so
    the two metrics are on the same clock and can be compared directly in
    filtering and composite scoring.

    Note: CAGR in trading-day years is slightly higher than a calendar-day CAGR
    for the same backtest (~1.45× exponent).  This is expected — document the
    convention when reporting externally.

    Returns:
        0.0   — fewer than 2 bars (no elapsed time).
       -1.0   — portfolio reached zero or went negative.
    """
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    if initial == 0.0:
        raise ValueError("initial_equity is 0 — check initial_cash on SimulationRunRequest.")
    bars = _duration_bars(equity_curve)
    periods = bars - 1  # number of bar-to-bar steps
    if periods < 1:
        return 0.0
    ratio = final / initial
    if ratio <= 0.0:
        return -1.0
    return float(ratio ** (bars_per_year / periods)) - 1.0


def return_metrics(
    equity_curve: pd.DataFrame,
    bars_per_year: int = BARS_PER_YEAR,
) -> ReturnMetrics:
    """Compute all return metrics in one call."""
    _validate(equity_curve)
    initial, final = _endpoints(equity_curve)
    return ReturnMetrics(
        total_return=total_return(equity_curve),
        cagr=cagr(equity_curve, bars_per_year),
        initial_equity=initial,
        final_equity=final,
        duration_bars=_duration_bars(equity_curve),
    )

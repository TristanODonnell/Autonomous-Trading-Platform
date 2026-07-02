"""
Shared metric computation utilities for platform backtest visualization.

All functions operate on pandas Series indexed by date and return Series
or scalar values. Units are documented per function.

Rolling windows default to 30 trading days throughout.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_ANNUAL_FACTOR = 252  # trading days per year


# ---------------------------------------------------------------------------
# Rolling metrics
# ---------------------------------------------------------------------------


def rolling_returns(equity: pd.Series, window: int = 30) -> pd.Series:
    """Rolling total return (fraction) over `window` trading days."""
    return equity.pct_change(window)


def rolling_volatility(daily_returns: pd.Series, window: int = 30) -> pd.Series:
    """Rolling annualized volatility (fraction)."""
    return daily_returns.rolling(window, min_periods=max(2, window // 3)).std() * np.sqrt(
        _ANNUAL_FACTOR
    )


def rolling_sharpe(
    daily_returns: pd.Series,
    window: int = 30,
    rfr_annual: float = 0.053,
) -> pd.Series:
    """Rolling annualized Sharpe ratio."""
    rfr_daily = rfr_annual / _ANNUAL_FACTOR
    excess = daily_returns - rfr_daily
    roll_mean = excess.rolling(window, min_periods=max(2, window // 3)).mean()
    roll_std = daily_returns.rolling(window, min_periods=max(2, window // 3)).std()
    sharpe = roll_mean / roll_std.replace(0, np.nan) * np.sqrt(_ANNUAL_FACTOR)
    return sharpe.clip(-5, 5)  # clip extremes for readability


def rolling_beta(
    returns_p: pd.Series,
    returns_b: pd.Series,
    window: int = 30,
) -> pd.Series:
    """Rolling beta of platform vs benchmark."""
    cov = returns_p.rolling(window, min_periods=max(2, window // 3)).cov(returns_b)
    var = returns_b.rolling(window, min_periods=max(2, window // 3)).var()
    return (cov / var.replace(0, np.nan)).clip(-3, 3)


def rolling_alpha(
    returns_p: pd.Series,
    returns_b: pd.Series,
    window: int = 30,
    rfr_annual: float = 0.053,
) -> pd.Series:
    """Rolling annualized alpha vs benchmark."""
    rfr_daily = rfr_annual / _ANNUAL_FACTOR
    beta = rolling_beta(returns_p, returns_b, window)
    roll_p = returns_p.rolling(window, min_periods=max(2, window // 3)).mean()
    roll_b = returns_b.rolling(window, min_periods=max(2, window // 3)).mean()
    alpha_daily = roll_p - rfr_daily - beta * (roll_b - rfr_daily)
    return alpha_daily * _ANNUAL_FACTOR


def rolling_drawdown(equity: pd.Series) -> pd.Series:
    """Rolling drawdown from running peak (0 to -1)."""
    peak = equity.expanding().max()
    return (equity - peak) / peak.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Scalar metrics with date information
# ---------------------------------------------------------------------------


def max_drawdown_details(equity: pd.Series) -> dict:
    """
    Compute max drawdown and associated dates.

    Returns a dict with keys:
        max_dd_pct          — max drawdown as % (negative)
        peak_date           — timestamp of equity peak before worst trough
        trough_date         — timestamp of worst trough
        recovery_date       — timestamp of first full recovery (or None)
        peak_to_trough_days — calendar days from peak to trough
        trough_to_recovery_days — calendar days from trough to recovery (or None)
        recovered           — bool
    """
    if len(equity) < 2:
        return {
            "max_dd_pct": 0.0,
            "peak_date": None,
            "trough_date": None,
            "recovery_date": None,
            "peak_to_trough_days": 0,
            "trough_to_recovery_days": None,
            "recovered": True,
        }

    running_peak = equity.expanding().max()
    drawdown = (equity - running_peak) / running_peak.replace(0, np.nan)

    trough_date = drawdown.idxmin()
    max_dd = float(drawdown.min())

    # Peak: last point at or before trough where equity matched the running peak
    pre_trough_equity = equity[:trough_date]
    pre_trough_peak = running_peak[:trough_date]
    at_peak = pre_trough_equity[pre_trough_equity >= pre_trough_peak]
    peak_date = at_peak.index[-1] if len(at_peak) > 0 else equity.index[0]

    # Recovery: first point after trough where equity meets/exceeds the peak value at trough
    peak_value = float(running_peak[trough_date])
    post_trough = equity[trough_date:]
    recovered_pts = post_trough[post_trough >= peak_value]
    recovery_date = recovered_pts.index[0] if len(recovered_pts) > 0 else None

    return {
        "max_dd_pct": round(max_dd * 100, 2),
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "peak_to_trough_days": (trough_date - peak_date).days,
        "trough_to_recovery_days": (recovery_date - trough_date).days if recovery_date else None,
        "recovered": recovery_date is not None,
    }


def compute_cagr(start_value: float, end_value: float, n_days: int) -> float:
    """CAGR as a fraction."""
    if n_days <= 0 or start_value <= 0:
        return 0.0
    years = n_days / _ANNUAL_FACTOR
    return float((end_value / start_value) ** (1 / years) - 1)


def compute_sharpe(
    daily_returns: np.ndarray | pd.Series,
    rfr_annual: float = 0.053,
) -> float:
    """Annualized Sharpe ratio."""
    r = np.asarray(daily_returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    ann_r = np.mean(r) * _ANNUAL_FACTOR
    ann_v = np.std(r, ddof=1) * np.sqrt(_ANNUAL_FACTOR)
    return float((ann_r - rfr_annual) / ann_v) if ann_v > 0 else 0.0


def hurdle_final_value(starting_cash: float, hurdle_pct: float) -> float:
    """Ending value required to clear a simple total return hurdle."""
    return starting_cash * (1 + hurdle_pct / 100)

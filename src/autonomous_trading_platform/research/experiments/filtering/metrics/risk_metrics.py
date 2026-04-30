"""
Computes Sharpe, Sortino, volatility and max drawdown from a simulation equity curve.

equity_curve columns: timestamp, equity, cash, positions_value, drawdown

Annualisation: 252 trading days × 78 five-minute bars/session = 19_656 bars/year.
Pass a custom bars_per_year if adding daily or 1-minute bar support.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_DEFAULT_BARS_PER_YEAR: int = 252 * 78  # 19_656


@dataclass(frozen=True, slots=True)
class RiskMetrics:
    sharpe_ratio: float  # mean(excess) / std(excess) * sqrt(bars_per_year)
    sortino_ratio: float  # mean(excess) / downside_std * sqrt(bars_per_year)
    volatility: float  # std(bar_returns) * sqrt(bars_per_year)
    max_drawdown: float  # min((equity - peak) / peak) — negative decimal
    bars_per_year: int
    risk_free_rate: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(equity_curve: pd.DataFrame) -> None:
    if equity_curve is None or equity_curve.empty:
        raise ValueError("equity_curve is empty.")
    for col in ("timestamp", "equity"):
        if col not in equity_curve.columns:
            raise ValueError(f"equity_curve missing column: '{col}'")


def _bar_returns(equity_curve: pd.DataFrame) -> np.ndarray:
    """Bar-to-bar % returns. Length = len(curve) - 1."""
    equity = equity_curve.sort_values("timestamp")["equity"].to_numpy(dtype=np.float64)
    return np.diff(equity) / equity[:-1]


def _bar_rf(risk_free_rate: float, bars_per_year: int) -> float:
    """Convert annual risk-free rate to per-bar rate geometrically."""
    return float((1.0 + risk_free_rate) ** (1.0 / bars_per_year)) - 1.0


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def volatility(
    equity_curve: pd.DataFrame,
    bars_per_year: int = _DEFAULT_BARS_PER_YEAR,
) -> float:
    """std(bar_returns, ddof=1) * sqrt(bars_per_year). Returns 0.0 if < 2 bars."""
    _validate(equity_curve)
    r = _bar_returns(equity_curve)
    if len(r) < 2:
        return 0.0
    return float(np.std(r, ddof=1) * np.sqrt(bars_per_year))


def max_drawdown(equity_curve: pd.DataFrame) -> float:
    """
    min((equity - running_peak) / running_peak).
    Returns a negative decimal, e.g. -0.20 == -20%. Returns 0.0 if no drawdown.
    """
    _validate(equity_curve)
    equity = equity_curve.sort_values("timestamp")["equity"].to_numpy(dtype=np.float64)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peak == 0.0, 0.0, (equity - peak) / peak)
    return float(np.min(drawdowns))


def sharpe_ratio(
    equity_curve: pd.DataFrame,
    risk_free_rate: float = 0.0,
    bars_per_year: int = _DEFAULT_BARS_PER_YEAR,
) -> float:
    """
    mean(excess) / std(excess) * sqrt(bars_per_year).
    excess = bar_returns - per_bar_rf.
    Returns 0.0 if < 2 bars or zero variance.
    """
    _validate(equity_curve)
    r = _bar_returns(equity_curve)
    if len(r) < 2:
        return 0.0
    excess = r - _bar_rf(risk_free_rate, bars_per_year)
    std = float(np.std(excess, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(bars_per_year))


def sortino_ratio(
    equity_curve: pd.DataFrame,
    risk_free_rate: float = 0.0,
    bars_per_year: int = _DEFAULT_BARS_PER_YEAR,
) -> float:
    """
    mean(excess) / downside_std * sqrt(bars_per_year).
    downside_std = sqrt(mean(excess[excess < 0]^2))  — semi-deviation, not std.
    Returns 0.0 if < 2 bars or no downside bars (theoretically inf — capped at 0).
    """
    _validate(equity_curve)
    r = _bar_returns(equity_curve)
    if len(r) < 2:
        return 0.0
    excess = r - _bar_rf(risk_free_rate, bars_per_year)
    downside = excess[excess < 0.0]
    if len(downside) == 0:
        return 0.0
    downside_std = float(np.sqrt(np.mean(downside**2)))
    if downside_std == 0.0:
        return 0.0
    return float(np.mean(excess) / downside_std * np.sqrt(bars_per_year))


def risk_metrics(
    equity_curve: pd.DataFrame,
    risk_free_rate: float = 0.0,
    bars_per_year: int = _DEFAULT_BARS_PER_YEAR,
) -> RiskMetrics:
    """Compute all risk metrics in one call."""
    _validate(equity_curve)
    return RiskMetrics(
        sharpe_ratio=sharpe_ratio(equity_curve, risk_free_rate, bars_per_year),
        sortino_ratio=sortino_ratio(equity_curve, risk_free_rate, bars_per_year),
        volatility=volatility(equity_curve, bars_per_year),
        max_drawdown=max_drawdown(equity_curve),
        bars_per_year=bars_per_year,
        risk_free_rate=risk_free_rate,
    )

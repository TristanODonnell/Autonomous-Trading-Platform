"""
Computes variance across time windows, consistency score and drawdown recovery time.

equity_curve columns: timestamp, equity, cash, positions_value, drawdown

Three lenses:
  1. windowed_returns   — split curve into N equal chunks; return per chunk
  2. consistency_score  — fraction of chunks with return > 0  (== w4 in scoring)
  3. recovery_times     — bars from drawdown start to equity returning >= prior peak
                          Open episodes at simulation end are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_DEFAULT_N_WINDOWS: int = 4  # quarterly by default


@dataclass(frozen=True, slots=True)
class StabilityMetrics:
    n_windows: int
    windowed_returns: list[float]  # one decimal return per window
    return_variance: float  # var(windowed_returns, ddof=1); lower == more stable
    consistency_score: float  # profitable_windows / total_windows  ∈ [0, 1]
    drawdown_recovery_times: list[int]  # bars to recover per completed episode
    mean_recovery_bars: float
    median_recovery_bars: float
    max_recovery_bars: int  # 0 when no drawdowns occurred


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate(equity_curve: pd.DataFrame) -> None:
    if equity_curve is None or equity_curve.empty:
        raise ValueError("equity_curve is empty.")
    for col in ("timestamp", "equity"):
        if col not in equity_curve.columns:
            raise ValueError(f"equity_curve missing column: '{col}'")


def _equity_array(equity_curve: pd.DataFrame) -> np.ndarray:
    return equity_curve.sort_values("timestamp")["equity"].to_numpy(dtype=np.float64)


def _chunks(equity: np.ndarray, n: int) -> list[np.ndarray]:
    """Split into n equal chunks; drop any with < 2 points (no return computable)."""
    if len(equity) < 2:
        return []
    chunks = np.array_split(equity, min(n, len(equity) - 1))
    return [c for c in chunks if len(c) >= 2]


def _chunk_return(c: np.ndarray) -> float:
    """(last - first) / first. Returns 0.0 if first == 0."""
    return 0.0 if c[0] == 0.0 else float((c[-1] - c[0]) / c[0])


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def windowed_returns(
    equity_curve: pd.DataFrame,
    n_windows: int = _DEFAULT_N_WINDOWS,
) -> list[float]:
    """
    Split equity curve into n_windows equal chunks; return (last-first)/first per chunk.
    Returns [] if curve has < 2 bars. May return fewer than n_windows if curve is short.
    """
    _validate(equity_curve)
    return [_chunk_return(c) for c in _chunks(_equity_array(equity_curve), n_windows)]


def return_variance(
    equity_curve: pd.DataFrame,
    n_windows: int = _DEFAULT_N_WINDOWS,
) -> float:
    """var(windowed_returns, ddof=1). Returns 0.0 if < 2 usable windows."""
    _validate(equity_curve)
    r = windowed_returns(equity_curve, n_windows)
    return float(np.var(r, ddof=1)) if len(r) >= 2 else 0.0


def consistency_score(
    equity_curve: pd.DataFrame,
    n_windows: int = _DEFAULT_N_WINDOWS,
) -> float:
    """
    profitable_windows / total_windows.
    This is the Consistency term (w4) in:
        score = w1*Sharpe + w2*Return - w3*abs(Drawdown) + w4*Consistency
    Returns 0.0 if no usable windows.
    """
    _validate(equity_curve)
    r = windowed_returns(equity_curve, n_windows)
    return sum(1 for x in r if x > 0) / len(r) if r else 0.0


def drawdown_recovery_times(equity_curve: pd.DataFrame) -> list[int]:
    """
    For each completed drawdown episode, count bars from first bar below peak
    to first bar back at or above that peak. Open episodes (still underwater
    at simulation end) are excluded. Returns [] if no drawdowns occurred.
    """
    _validate(equity_curve)
    equity = _equity_array(equity_curve)
    peak = equity[0]
    in_drawdown = False
    start_bar = 0
    times: list[int] = []

    for i, val in enumerate(equity):
        if val >= peak:
            if in_drawdown:
                times.append(i - start_bar)
                in_drawdown = False
            peak = val
        else:
            if not in_drawdown:
                in_drawdown = True
                start_bar = i

    return times


def stability_metrics(
    equity_curve: pd.DataFrame,
    n_windows: int = _DEFAULT_N_WINDOWS,
) -> StabilityMetrics:
    """Compute all stability metrics in one call."""
    _validate(equity_curve)
    w = windowed_returns(equity_curve, n_windows)
    rec = drawdown_recovery_times(equity_curve)
    return StabilityMetrics(
        n_windows=n_windows,
        windowed_returns=w,
        return_variance=float(np.var(w, ddof=1)) if len(w) >= 2 else 0.0,
        consistency_score=sum(1 for x in w if x > 0) / len(w) if w else 0.0,
        drawdown_recovery_times=rec,
        mean_recovery_bars=float(np.mean(rec)) if rec else 0.0,
        median_recovery_bars=float(np.median(rec)) if rec else 0.0,
        max_recovery_bars=int(max(rec)) if rec else 0,
    )

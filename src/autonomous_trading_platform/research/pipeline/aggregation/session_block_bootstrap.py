"""
Session-block bootstrap of a simulated equity curve.

What it measures
----------------
A backtest is one draw from history. Resampling its returns shows how much the
headline metrics could have differed over a history of the same length, which
is the uncertainty in the estimate, not a property of the strategy's logic.

Method
------
1. Bar returns of the equity curve, grouped by session (UTC date of the bar
   that ends the return, so an overnight gap belongs to the session it opens).
2. Each resampled path is the same number of sessions, drawn whole and with
   replacement. Whole sessions keep the intraday autocorrelation that single
   bars would destroy (single bars understate the Sharpe spread).
3. Sharpe, total return and max drawdown per path, with the formulas of
   experiments/filtering/metrics/risk_metrics.py.

Reordering sessions without replacement is not a substitute: it cannot move
Sharpe or total return at all, only drawdown.

Sessions have unequal bar counts (early closes, missing bars). They are stored
zero-padded to a rectangle: a zero return changes neither the cumulative
product nor the drawdown. Sharpe would be biased by the padding, so it is
computed from per-session sums over the real bars only.

This module knows nothing about the pipeline, stages or the simulator.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# One session resampled is always that session: every path would be identical.
MIN_SESSIONS = 2

# Budget for one chunk of bar-level paths (a second array of the same size is
# alive at the peak). 2,000 paths over 756 five-minute sessions would be about
# 940 MB as a single array.
_DEFAULT_CHUNK_BYTES = 32 * 1024 * 1024

# A per-bar return std below this is rounding noise on a flat curve, not risk.
_MIN_RETURN_STD = 1e-12


@dataclass(frozen=True)
class SessionReturns:
    """Bar returns of one equity curve, one row per session, zero-padded."""

    padded: np.ndarray  # (n_sessions, max_bars_in_a_session)
    bar_counts: np.ndarray  # (n_sessions,) real bar returns in each session

    @property
    def n_sessions(self) -> int:
        return int(self.bar_counts.shape[0])


@dataclass(frozen=True)
class BootstrapPaths:
    """Per-path metrics, each of shape (n_paths,)."""

    sharpe: np.ndarray
    total_return: np.ndarray
    max_drawdown: np.ndarray  # negative decimal, e.g. -0.20
    n_sessions: int  # sessions in the source curve

    @property
    def n_paths(self) -> int:
        return int(self.sharpe.shape[0])


def session_returns(equity_curve: pd.DataFrame) -> SessionReturns:
    """Group an equity curve's bar returns by session. Raises ValueError if unusable."""
    if equity_curve is None or equity_curve.empty:
        raise ValueError("equity_curve is empty")
    for col in ("timestamp", "equity"):
        if col not in equity_curve.columns:
            raise ValueError(f"equity_curve missing column: '{col}'")

    frame = equity_curve.sort_values("timestamp")
    equity = frame["equity"].to_numpy(dtype=np.float64)
    if len(equity) < 2:
        raise ValueError("equity_curve has fewer than 2 points")
    if not np.all(np.isfinite(equity)) or np.any(equity <= 0.0):
        raise ValueError("equity_curve has non-finite or non-positive equity")

    returns = np.diff(equity) / equity[:-1]
    session_day = pd.to_datetime(frame["timestamp"], utc=True).dt.normalize().iloc[1:]
    # Sorted by timestamp, so codes are non-decreasing and each session is contiguous.
    codes, _ = pd.factorize(session_day, sort=True)

    bar_counts = np.bincount(codes)
    first_bar = np.cumsum(bar_counts) - bar_counts
    position = np.arange(len(returns)) - first_bar[codes]
    padded = np.zeros((len(bar_counts), int(bar_counts.max())), dtype=np.float64)
    padded[codes, position] = returns
    return SessionReturns(padded=padded, bar_counts=bar_counts)


def evaluate_session_paths(
    sessions: SessionReturns,
    draws: np.ndarray,
    *,
    bars_per_year: int,
    risk_free_rate: float = 0.0,
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
) -> BootstrapPaths:
    """
    Metrics for paths given as session indices, shape (n_paths, sessions_per_path).

    The identity draw (0, 1, ..., n-1) reproduces the source curve's own Sharpe,
    total return and max drawdown.
    """
    n_paths, sessions_per_path = draws.shape
    max_bars = sessions.padded.shape[1]

    # --- Sharpe from per-session sums (real bars only) -----------------------
    # Same per-bar risk-free convention as risk_metrics._bar_rf.
    bar_rf = float((1.0 + risk_free_rate) ** (1.0 / bars_per_year)) - 1.0
    is_real_bar = np.arange(max_bars)[None, :] < sessions.bar_counts[:, None]
    excess = sessions.padded - bar_rf
    # Centre on the overall mean so the variance below does not subtract two
    # nearly equal numbers.
    centre = float(excess[is_real_bar].mean())
    centred = np.where(is_real_bar, excess - centre, 0.0)
    session_sum = centred.sum(axis=1)
    session_sum_sq = (centred**2).sum(axis=1)

    n_bars = sessions.bar_counts[draws].sum(axis=1).astype(np.float64)
    path_sum = session_sum[draws].sum(axis=1)
    path_sum_sq = session_sum_sq[draws].sum(axis=1)

    enough_bars = n_bars >= 2
    safe_n = np.where(enough_bars, n_bars, 2.0)
    mean = centre + path_sum / safe_n
    variance = (path_sum_sq - path_sum**2 / safe_n) / (safe_n - 1.0)
    std = np.sqrt(np.clip(variance, 0.0, None))
    has_risk = enough_bars & (std > _MIN_RETURN_STD)
    sharpe = np.zeros(n_paths, dtype=np.float64)
    np.divide(mean, std, out=sharpe, where=has_risk)
    sharpe *= np.sqrt(bars_per_year)

    # --- Total return and max drawdown from bar-level paths, in chunks -------
    path_len = sessions_per_path * max_bars
    chunk = max(1, chunk_bytes // (path_len * 8))
    total_return = np.empty(n_paths, dtype=np.float64)
    max_drawdown = np.empty(n_paths, dtype=np.float64)
    for lo in range(0, n_paths, chunk):
        hi = min(lo + chunk, n_paths)
        growth = sessions.padded[draws[lo:hi]].reshape(hi - lo, path_len)
        growth += 1.0
        np.cumprod(growth, axis=1, out=growth)  # equity relative to a start of 1.0
        total_return[lo:hi] = growth[:, -1] - 1.0
        peak = np.maximum.accumulate(growth, axis=1)
        np.maximum(peak, 1.0, out=peak)  # the starting equity is a peak too
        growth /= peak
        max_drawdown[lo:hi] = growth.min(axis=1) - 1.0

    return BootstrapPaths(
        sharpe=sharpe,
        total_return=total_return,
        max_drawdown=max_drawdown,
        n_sessions=sessions.n_sessions,
    )


def session_block_bootstrap(
    equity_curve: pd.DataFrame,
    *,
    n_paths: int,
    seed: int,
    bars_per_year: int,
    risk_free_rate: float = 0.0,
    chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
) -> BootstrapPaths:
    """
    Resample an equity curve in whole sessions with replacement.

    Deterministic for a given (equity_curve, n_paths, seed). Every session
    index is drawn before any path is computed, so chunk_bytes changes memory
    use only, never the result.

    Raises ValueError when the curve cannot be resampled (empty, non-positive
    equity, fewer than MIN_SESSIONS sessions). Callers treat that as missing
    evidence.
    """
    if n_paths < 1:
        raise ValueError(f"n_paths must be >= 1, got {n_paths}")
    sessions = session_returns(equity_curve)
    if sessions.n_sessions < MIN_SESSIONS:
        raise ValueError(
            f"equity_curve spans {sessions.n_sessions} session(s); "
            f"resampling needs at least {MIN_SESSIONS}"
        )
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, sessions.n_sessions, size=(n_paths, sessions.n_sessions))
    return evaluate_session_paths(
        sessions,
        draws,
        bars_per_year=bars_per_year,
        risk_free_rate=risk_free_rate,
        chunk_bytes=chunk_bytes,
    )

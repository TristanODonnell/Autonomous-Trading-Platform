"""
Synthetic financial data generator for platform backtest storytelling.

When a real artifact has flat portfolio values (trading not yet wired),
this module generates realistic equity curves, returns, and execution
statistics that align with the artifact's date range and timeline events.

The generated data:
- Uses the artifact's actual trading dates (no calendar guessing)
- Anchors drawdown episodes to real safety halt / controls pause events
- Produces correlated platform + benchmark series (platform outperforms on
  risk-adjusted basis as a realistic storytelling scenario)
- Is reproducible: seed derived from replay_id
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from visualization.loader import ArtifactData

# ---------------------------------------------------------------------------
# Scenario parameters
# ---------------------------------------------------------------------------

_PLATFORM_ANNUAL_RETURN = 0.24  # 24% annual gross return
_PLATFORM_ANNUAL_VOL = 0.14  # 14% annualised vol → Sharpe ~1.6
_BENCHMARK_ANNUAL_RETURN = 0.21  # SPY-like 21%
_BENCHMARK_ANNUAL_VOL = 0.16  # 16% vol → Sharpe ~1.2
_CORRELATION = 0.72  # platform / benchmark correlation
_RISK_FREE_RATE = 0.053  # 5.3% risk-free (2024 Fed funds proxy)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def augment(data: ArtifactData, starting_cash: float = 100_000.0) -> ArtifactData:
    """
    Augment ArtifactData with synthetic financial performance.

    Adds the following columns to tick_df:
        portfolio_value_s  — simulated platform equity ($)
        benchmark_value_s  — simulated SPY benchmark equity ($)
        cash_value_s       — risk-free / cash line ($)
        daily_return_s     — platform daily return
        benchmark_return_s — benchmark daily return
        drawdown_s         — platform drawdown from peak (0 to -1)
        benchmark_drawdown_s

    Also adds:
        starting_cash      — recorded on the data object
        synthetic_stats    — dict of computed performance statistics

    Does NOT overwrite real data if portfolio_value already varies.
    """
    if not data.is_synthetic:
        _add_real_derived_columns(data, starting_cash)
        return data

    seed = _seed_from_replay_id(data.replay_id)
    rng = np.random.default_rng(seed)

    dates = data.tick_df.index
    n = len(dates)

    if n == 0:
        return data

    # Build correlated daily return streams via Cholesky decomposition
    daily_mu_p = _PLATFORM_ANNUAL_RETURN / 252
    daily_mu_b = _BENCHMARK_ANNUAL_RETURN / 252
    daily_sig_p = _PLATFORM_ANNUAL_VOL / np.sqrt(252)
    daily_sig_b = _BENCHMARK_ANNUAL_VOL / np.sqrt(252)

    cov = np.array(
        [
            [daily_sig_p**2, _CORRELATION * daily_sig_p * daily_sig_b],
            [_CORRELATION * daily_sig_p * daily_sig_b, daily_sig_b**2],
        ]
    )
    L = np.linalg.cholesky(cov)
    z = rng.standard_normal((n, 2))
    shocks = z @ L.T  # (n, 2) — correlated shocks

    raw_returns_p = daily_mu_p + shocks[:, 0]
    raw_returns_b = daily_mu_b + shocks[:, 1]

    years = n / 252

    # Drift-correct benchmark (no shocks applied to it)
    target_total_b = (1 + _BENCHMARK_ANNUAL_RETURN) ** years - 1
    target_daily_b = (1 + target_total_b) ** (1 / n) - 1
    raw_returns_b = raw_returns_b + (target_daily_b - np.mean(raw_returns_b))

    # Anchor drawdown episodes to real halt/pause events from timeline
    raw_returns_p = _apply_event_shocks(raw_returns_p, dates, data.timeline_events, rng)

    # Drift-correct platform AFTER shocks so event drags are preserved in
    # shape but the overall path still reaches the target annual return.
    target_total_p = (1 + _PLATFORM_ANNUAL_RETURN) ** years - 1
    target_daily_p = (1 + target_total_p) ** (1 / n) - 1
    raw_returns_p = raw_returns_p + (target_daily_p - np.mean(raw_returns_p))

    # Build cumulative equity paths
    equity_p = starting_cash * np.cumprod(1 + raw_returns_p)
    equity_b = starting_cash * np.cumprod(1 + raw_returns_b)
    equity_c = starting_cash * np.cumprod(np.full(n, 1 + _RISK_FREE_RATE / 252))

    drawdown_p = _compute_drawdown(equity_p)
    drawdown_b = _compute_drawdown(equity_b)

    df = data.tick_df.copy()
    df["portfolio_value_s"] = equity_p
    df["benchmark_value_s"] = equity_b
    df["cash_value_s"] = equity_c
    df["daily_return_s"] = raw_returns_p
    df["benchmark_return_s"] = raw_returns_b
    df["drawdown_s"] = drawdown_p
    df["benchmark_drawdown_s"] = drawdown_b

    # Synthetic fill quality (slippage, costs)
    df = _add_synthetic_execution(df, rng, n)

    data.tick_df = df
    data.starting_cash = starting_cash  # type: ignore[attr-defined]
    data.synthetic_stats = _compute_stats(  # type: ignore[attr-defined]
        raw_returns_p,
        equity_p,
        drawdown_p,
        raw_returns_b,
        equity_b,
        drawdown_b,
        starting_cash,
        data.start_date,
        data.end_date,
    )
    return data


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _seed_from_replay_id(replay_id: str) -> int:
    """Derive a reproducible integer seed from the replay_id string."""
    h = 0
    for ch in replay_id or "default":
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h or 42


def _apply_event_shocks(
    returns: np.ndarray,
    dates: pd.DatetimeIndex,
    timeline_events: list[dict],
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Inject short drawdown episodes aligned with real halt / pause events.
    The recovery follows naturally from the GBM drift.
    """
    date_to_idx = {d.date(): i for i, d in enumerate(dates)}
    returns = returns.copy()

    halt_types = {"safety_emergency_halt", "controls_paused"}

    for ev in timeline_events:
        if ev.get("event_type") not in halt_types:
            continue
        ev_date_str = ev.get("date")
        if not ev_date_str:
            continue
        try:
            ev_date = date.fromisoformat(ev_date_str)
        except ValueError:
            continue

        idx = date_to_idx.get(ev_date)
        if idx is None:
            continue

        # Inject a 3-7 day stress shock around the halt date
        shock_len = int(rng.integers(3, 8))
        shock_start = max(0, idx - 1)
        shock_end = min(len(returns), shock_start + shock_len)
        severity = float(rng.uniform(0.008, 0.025))

        for j in range(shock_start, shock_end):
            decay = 1.0 - (j - shock_start) / shock_len
            returns[j] -= severity * decay

    return returns


def _compute_drawdown(equity: np.ndarray) -> np.ndarray:
    """Compute drawdown series (0 to -1) from an equity curve."""
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return dd


def _generate_synthetic_benchmark(
    dates: pd.DatetimeIndex,
    starting_cash: float,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate a correlated-GBM SPY-proxy benchmark series.

    No real external SPY price data is loaded by this package (see
    methodology report), so the benchmark line is always this synthetic
    proxy — used alongside either real or synthetic platform equity.
    Returns (equity_b, drawdown_b, raw_returns_b).
    """
    rng = np.random.default_rng(seed)
    n = len(dates)

    daily_mu_b = _BENCHMARK_ANNUAL_RETURN / 252
    daily_sig_b = _BENCHMARK_ANNUAL_VOL / np.sqrt(252)
    raw_returns_b = daily_mu_b + rng.standard_normal(n) * daily_sig_b

    years = n / 252
    target_total_b = (1 + _BENCHMARK_ANNUAL_RETURN) ** years - 1
    target_daily_b = (1 + target_total_b) ** (1 / n) - 1
    raw_returns_b = raw_returns_b + (target_daily_b - np.mean(raw_returns_b))

    equity_b = starting_cash * np.cumprod(1 + raw_returns_b)
    drawdown_b = _compute_drawdown(equity_b)
    return equity_b, drawdown_b, raw_returns_b


def _add_synthetic_execution(
    df: pd.DataFrame,
    rng: np.random.Generator,
    n: int,
) -> pd.DataFrame:
    """Add per-tick synthetic execution quality metrics."""
    base_slippage = 4.2  # bps average
    base_vol = 1.8  # bps std dev

    df["avg_slippage_bps_s"] = np.abs(rng.normal(base_slippage, base_vol, n)).clip(0.5, 15.0)
    df["adverse_fill_rate_s"] = np.clip(
        rng.beta(2, 18, n),  # ~10% adverse rate
        0.0,
        0.35,
    )
    df["avg_latency_ms_s"] = np.abs(rng.normal(42, 12, n)).clip(10, 200)
    return df


def _add_real_derived_columns(data: ArtifactData, starting_cash: float) -> None:
    """When real trading data exists, derive supplemental columns."""
    df = data.tick_df
    if "portfolio_value" not in df.columns:
        return
    pv = df["portfolio_value"].copy()
    # A literal 0.0 on the very first tick(s) is a pre-seed snapshot artifact
    # (the initial cash snapshot commits after tick 1's portfolio snapshot is
    # taken) — not a real drop to zero capital. Treat leading zeros as missing.
    leading_zero_mask = (pv == 0) & (pv.index < pv[pv != 0].index.min())
    pv[leading_zero_mask] = np.nan
    pv = pv.ffill().fillna(starting_cash)
    df["portfolio_value_s"] = pv
    df["daily_return_s"] = pv.pct_change().fillna(0)
    df["drawdown_s"] = _compute_drawdown(pv.values)

    n = len(df.index)
    if n > 0:
        seed = _seed_from_replay_id(data.replay_id)
        equity_b, drawdown_b, raw_returns_b = _generate_synthetic_benchmark(
            df.index, starting_cash, seed
        )
        df["benchmark_value_s"] = equity_b
        df["benchmark_drawdown_s"] = drawdown_b
        df["benchmark_return_s"] = raw_returns_b
        df["cash_value_s"] = starting_cash * np.cumprod(np.full(n, 1 + _RISK_FREE_RATE / 252))

        data.synthetic_stats = _compute_stats(  # type: ignore[attr-defined]
            df["daily_return_s"].values,
            df["portfolio_value_s"].values,
            df["drawdown_s"].values,
            raw_returns_b,
            equity_b,
            drawdown_b,
            starting_cash,
            data.start_date,
            data.end_date,
        )

        # Execution-quality metrics (slippage, adverse-fill rate, latency) are
        # not captured per-fill in the artifact, so — like the benchmark line
        # above — this is always a modeled/synthetic estimate, disclosed as
        # such in the methodology report, regardless of whether the rest of
        # the run used real fills.
        rng = np.random.default_rng(seed)
        df = _add_synthetic_execution(df, rng, n)

    data.starting_cash = starting_cash  # type: ignore[attr-defined]


def _compute_stats(
    returns_p: np.ndarray,
    equity_p: np.ndarray,
    drawdown_p: np.ndarray,
    returns_b: np.ndarray,
    equity_b: np.ndarray,
    drawdown_b: np.ndarray,
    starting_cash: float,
    start: date,
    end: date,
) -> dict:
    """Compute the full performance statistics dictionary."""
    n_days = len(returns_p)
    years = n_days / 252

    def _sharpe(r: np.ndarray) -> float:
        ann_r = np.mean(r) * 252
        ann_v = np.std(r, ddof=1) * np.sqrt(252)
        return (ann_r - _RISK_FREE_RATE) / ann_v if ann_v > 0 else 0.0

    def _sortino(r: np.ndarray) -> float:
        ann_r = np.mean(r) * 252
        neg = r[r < 0]
        downside_vol = np.std(neg, ddof=1) * np.sqrt(252) if len(neg) > 1 else 1e-9
        return float((ann_r - _RISK_FREE_RATE) / downside_vol)

    def _cagr(eq: np.ndarray) -> float:
        return float((eq[-1] / eq[0]) ** (1 / years) - 1) if years > 0 else 0.0

    def _max_dd(dd: np.ndarray) -> float:
        return float(np.min(dd))

    def _max_dd_duration(dd: np.ndarray) -> int:
        cur_len = 0
        max_len = 0
        for v in dd:
            if v < 0:
                cur_len += 1
                max_len = max(max_len, cur_len)
            else:
                cur_len = 0
        return max_len

    def _win_rate(r: np.ndarray) -> float:
        wins = np.sum(r > 0)
        return wins / len(r) if len(r) > 0 else 0.0

    def _calmar(cagr: float, max_dd: float) -> float:
        return abs(cagr / max_dd) if max_dd != 0 else 0.0

    total_return_p = equity_p[-1] / starting_cash - 1
    total_return_b = equity_b[-1] / starting_cash - 1
    cagr_p = _cagr(equity_p)
    cagr_b = _cagr(equity_b)
    vol_p = float(np.std(returns_p, ddof=1) * np.sqrt(252))
    vol_b = float(np.std(returns_b, ddof=1) * np.sqrt(252))
    sharpe_p = _sharpe(returns_p)
    sharpe_b = _sharpe(returns_b)
    sortino_p = _sortino(returns_p)
    max_dd_p = _max_dd(drawdown_p)
    max_dd_b = _max_dd(drawdown_b)

    # Tracking error and information ratio
    excess_returns = returns_p - returns_b
    tracking_error = float(np.std(excess_returns, ddof=1) * np.sqrt(252))
    info_ratio = (
        float(np.mean(excess_returns) * 252 / tracking_error) if tracking_error > 0 else 0.0
    )

    return {
        "total_return_pct": round(total_return_p * 100, 2),
        "total_return_pct_bench": round(total_return_b * 100, 2),
        "cagr_pct": round(cagr_p * 100, 2),
        "cagr_pct_bench": round(cagr_b * 100, 2),
        "sharpe_ratio": round(sharpe_p, 3),
        "sharpe_ratio_bench": round(sharpe_b, 3),
        "sortino_ratio": round(sortino_p, 3),
        "volatility_pct": round(vol_p * 100, 2),
        "volatility_pct_bench": round(vol_b * 100, 2),
        "max_drawdown_pct": round(max_dd_p * 100, 2),
        "max_drawdown_pct_bench": round(max_dd_b * 100, 2),
        "max_dd_duration_days": _max_dd_duration(drawdown_p),
        "calmar_ratio": round(_calmar(cagr_p, max_dd_p), 3),
        "win_rate_pct": round(_win_rate(returns_p) * 100, 1),
        "information_ratio": round(info_ratio, 3),
        "tracking_error_pct": round(tracking_error * 100, 2),
        "excess_return_pct": round((total_return_p - total_return_b) * 100, 2),
        "final_value": round(float(equity_p[-1]), 2),
        "final_value_bench": round(float(equity_b[-1]), 2),
        "starting_cash": starting_cash,
        "profit_loss_usd": round(float(equity_p[-1]) - starting_cash, 2),
        "n_trading_days": n_days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "risk_free_rate_pct": round(_RISK_FREE_RATE * 100, 2),
    }

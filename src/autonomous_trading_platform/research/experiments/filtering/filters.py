"""

Applies FilterConfig thresholds to computed metrics and returns a structured
pass/fail result per strategy.

core filters — Sharpe, drawdown, trade count, consistency, profit
          factor, win rate, return variance, total return.
robustness checks — per-window Sharpe floor, minimum profitable
          window count. Robustness runs on top of the core filters; a strategy
          must pass core before robustness is evaluated.

Usage
-----
    from .config import FilterConfig
    from .metrics.return_metrics import return_metrics
    from .metrics.risk_metrics import risk_metrics
    from .metrics.trade_metrics import trade_metrics
    from .metrics.stability_metrics import stability_metrics, windowed_returns
    from .filters import apply_filters

    result = apply_filters(
        rm=return_metrics(equity_curve),
        risk=risk_metrics(equity_curve),
        tm=trade_metrics(trade_logs),
        sm=stability_metrics(equity_curve),
        config=FilterConfig(),
        equity_curve=equity_curve,   # needed for per-window Sharpe (TASK-139)
    )

    if result.passed:
        ...
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import FilterConfig
from .metrics.return_metrics import ReturnMetrics
from .metrics.risk_metrics import RiskMetrics, sharpe_ratio
from .metrics.stability_metrics import StabilityMetrics
from .metrics.trade_metrics import TradeMetrics

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FilterResult:
    """
    Outcome of applying FilterConfig to one strategy's metrics.

    passed          True only when every active filter is satisfied.
    failures        Human-readable strings for each failed check.
                    Empty when passed == True.
    core_passed     True when all TASK-138 checks passed.
    robustness_passed  True when all TASK-139 checks passed (or none configured).
                       Always False when core_passed is False.
    """

    passed: bool
    failures: list[str]
    core_passed: bool
    robustness_passed: bool


# ---------------------------------------------------------------------------
# Internal — one check per filter
# ---------------------------------------------------------------------------


def _check_sharpe(risk: RiskMetrics, cfg: FilterConfig) -> str | None:
    # Sharpe must be >= min_sharpe
    if risk.sharpe_ratio < cfg.min_sharpe:
        return f"Sharpe {risk.sharpe_ratio:.3f} < min_sharpe {cfg.min_sharpe}"
    return None


def _check_drawdown(risk: RiskMetrics, cfg: FilterConfig) -> str | None:
    # max_drawdown is negative; must not be more negative than cfg.max_drawdown
    # e.g. -0.25 < -0.20 → fail
    if risk.max_drawdown < cfg.max_drawdown:
        return f"max_drawdown {risk.max_drawdown:.3f} < threshold {cfg.max_drawdown}"
    return None


def _check_trades(tm: TradeMetrics, cfg: FilterConfig) -> str | None:
    if tm.total_trades < cfg.min_trades:
        return f"total_trades {tm.total_trades} < min_trades {cfg.min_trades}"
    return None


def _check_consistency(sm: StabilityMetrics, cfg: FilterConfig) -> str | None:
    if sm.consistency_score < cfg.min_consistency_score:
        return (
            f"consistency_score {sm.consistency_score:.3f} "
            f"< min_consistency_score {cfg.min_consistency_score}"
        )
    return None


def _check_profit_factor(tm: TradeMetrics, cfg: FilterConfig) -> str | None:
    if cfg.min_profit_factor <= 0.0:
        return None
    if tm.profit_factor < cfg.min_profit_factor:
        return f"profit_factor {tm.profit_factor:.3f} < min_profit_factor {cfg.min_profit_factor}"
    return None


def _check_win_rate(tm: TradeMetrics, cfg: FilterConfig) -> str | None:
    # min_win_rate == 0.0 means disabled
    if cfg.min_win_rate <= 0.0:
        return None
    if tm.win_rate < cfg.min_win_rate:
        return f"win_rate {tm.win_rate:.3f} < min_win_rate {cfg.min_win_rate}"
    return None


def _check_return_variance(sm: StabilityMetrics, cfg: FilterConfig) -> str | None:
    # max_return_variance == None means disabled
    if cfg.max_return_variance is None:
        return None
    if sm.return_variance > cfg.max_return_variance:
        return (
            f"return_variance {sm.return_variance:.6f} "
            f"> max_return_variance {cfg.max_return_variance}"
        )
    return None


def _check_total_return(rm: ReturnMetrics, cfg: FilterConfig) -> str | None:
    if rm.total_return < cfg.min_total_return:
        return f"total_return {rm.total_return:.3f} < min_total_return {cfg.min_total_return}"
    return None


# ---------------------------------------------------------------------------
# Internal — TASK-139 robustness checks
# ---------------------------------------------------------------------------


def _check_robustness_per_window_sharpe(
    equity_curve: pd.DataFrame,
    sm: StabilityMetrics,
    cfg: FilterConfig,
) -> str | None:
    """
    Compute Sharpe for each time window independently and check each
    is >= robustness_min_sharpe. A strategy that only shines in one
    window fails here even if overall Sharpe is fine.
    Disabled when robustness_min_sharpe == 0.0.
    """
    if cfg.robustness_min_sharpe <= 0.0:
        return None

    sorted_curve = equity_curve.sort_values("timestamp").reset_index(drop=True)
    n = sm.n_windows
    window_size = max(1, len(sorted_curve) // n)
    failing_windows: list[int] = []

    for i in range(n):
        start = i * window_size
        end = start + window_size if i < n - 1 else len(sorted_curve)
        chunk = sorted_curve.iloc[start:end]

        if len(chunk) < 2:
            continue

        sr = sharpe_ratio(
            chunk,
            risk_free_rate=0.0,
            bars_per_year=252 * 78,
        )
        if sr < cfg.robustness_min_sharpe:
            failing_windows.append(i)

    if failing_windows:
        return f"per-window Sharpe below {cfg.robustness_min_sharpe} in windows: {failing_windows}"
    return None


def _check_robustness_profitable_windows(
    sm: StabilityMetrics,
    cfg: FilterConfig,
) -> str | None:
    """
    Absolute count of profitable windows must be >= robustness_min_profitable_windows.
    Disabled when robustness_min_profitable_windows == 0.
    """
    if cfg.robustness_min_profitable_windows <= 0:
        return None

    profitable = sum(1 for r in sm.windowed_returns if r > 0)
    if profitable < cfg.robustness_min_profitable_windows:
        return (
            f"profitable windows {profitable} "
            f"< robustness_min_profitable_windows "
            f"{cfg.robustness_min_profitable_windows}"
        )
    return None


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


def apply_filters(
    *,
    rm: ReturnMetrics,
    risk: RiskMetrics,
    tm: TradeMetrics,
    sm: StabilityMetrics,
    config: FilterConfig,
    equity_curve: pd.DataFrame,
) -> FilterResult:
    """
    Run all active filters against pre-computed metric dataclasses.

    Core filters (TASK-138) run first. If any fail, robustness checks
    (TASK-139) are skipped — there is no point stress-testing a strategy
    that already failed the baseline.

    Parameters
    ----------
    rm          ReturnMetrics from return_metrics()
    risk        RiskMetrics   from risk_metrics()
    tm          TradeMetrics  from trade_metrics()
    sm          StabilityMetrics from stability_metrics()
    config      FilterConfig  holding per-experiment thresholds
    equity_curve  raw equity_curve DataFrame — needed for per-window Sharpe

    Returns
    -------
    FilterResult with passed, failures, core_passed, robustness_passed.
    """
    core_failures: list[str] = []

    for result in (
        _check_sharpe(risk, config),
        _check_drawdown(risk, config),
        _check_trades(tm, config),
        _check_consistency(sm, config),
        _check_profit_factor(tm, config),
        _check_win_rate(tm, config),
        _check_return_variance(sm, config),
        _check_total_return(rm, config),
    ):
        if result is not None:
            core_failures.append(result)

    core_passed = len(core_failures) == 0

    # Robustness only runs when core passes
    robustness_failures: list[str] = []
    if core_passed:
        for result in (
            _check_robustness_per_window_sharpe(equity_curve, sm, config),
            _check_robustness_profitable_windows(sm, config),
        ):
            if result is not None:
                robustness_failures.append(result)

    robustness_passed = core_passed and len(robustness_failures) == 0
    all_failures = core_failures + robustness_failures

    return FilterResult(
        passed=len(all_failures) == 0,
        failures=all_failures,
        core_passed=core_passed,
        robustness_passed=robustness_passed,
    )

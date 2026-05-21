from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.research.analysis.regimes.regime_bucket import RegimeBucket
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    trade_metrics as compute_trade_metrics,
)

_MIN_BARS_FOR_METRICS = 2


@dataclass(frozen=True, slots=True)
class RegimeConditionedMetrics:
    bucket: RegimeBucket
    bar_count: int
    exposure_fraction: float
    total_return: float | None
    cagr: float | None
    sharpe: float | None
    sortino: float | None
    volatility: float | None
    max_drawdown: float | None
    trade_count: int
    win_rate: float | None
    expectancy: float | None
    profit_factor: float | None
    avg_win: float | None
    avg_loss: float | None
    trade_frequency: float | None
    avg_bar_return: float | None


def _metrics_from_returns(
    returns: np.ndarray,
    bars_per_year: int,
) -> dict[str, float | None]:
    n = len(returns)
    if n == 0:
        return {
            "total_return": None,
            "cagr": None,
            "sharpe": None,
            "sortino": None,
            "volatility": None,
            "max_drawdown": None,
            "avg_bar_return": None,
        }

    avg_bar_ret = float(np.mean(returns))

    # Compound all bar returns
    cumulative = float(np.prod(1.0 + returns))
    total_ret = cumulative - 1.0

    if cumulative <= 0.0:
        cagr: float | None = -1.0
    elif n < _MIN_BARS_FOR_METRICS:
        cagr = 0.0
    else:
        try:
            cagr = float(cumulative ** (bars_per_year / n)) - 1.0
        except OverflowError:
            cagr = float("inf")

    if n < _MIN_BARS_FOR_METRICS:
        return {
            "total_return": total_ret,
            "cagr": cagr,
            "sharpe": None,
            "sortino": None,
            "volatility": None,
            "max_drawdown": None,
            "avg_bar_return": avg_bar_ret,
        }

    std = float(np.std(returns, ddof=1))
    vol = std * np.sqrt(bars_per_year) if std > 0.0 else 0.0
    sharpe = float(np.mean(returns) / std * np.sqrt(bars_per_year)) if std > 0.0 else 0.0

    downside = returns[returns < 0.0]
    if len(downside) == 0:
        sortino: float | None = None
    else:
        # Same convention as risk_metrics.py: denominator uses total n, not n_downside
        downside_std = float(np.sqrt(np.sum(downside**2) / n))
        sortino = (
            float(np.mean(returns) / downside_std * np.sqrt(bars_per_year))
            if downside_std > 0.0
            else None
        )

    # Max drawdown within this regime's bar returns (running peak on synthetic equity)
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = np.where(peak == 0.0, 0.0, (equity - peak) / peak)
    mdd = float(np.min(drawdowns))

    return {
        "total_return": total_ret,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "volatility": vol,
        "max_drawdown": mdd,
        "avg_bar_return": avg_bar_ret,
    }


def compute_regime_metrics(
    *,
    bucket: RegimeBucket,
    bar_returns: np.ndarray,
    trades: pd.DataFrame,
    bar_count: int,
    total_bars: int,
    bars_per_year: int = BARS_PER_YEAR,
) -> RegimeConditionedMetrics:
    exposure_fraction = bar_count / total_bars if total_bars > 0 else 0.0

    ret_stats = _metrics_from_returns(bar_returns, bars_per_year)

    trade_count = 0
    win_rate: float | None = None
    expectancy: float | None = None
    profit_factor: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    trade_frequency: float | None = None

    if trades is not None and not trades.empty:
        try:
            tm = compute_trade_metrics(trades)
            trade_count = tm.total_trades
            if trade_count > 0:
                win_rate = tm.win_rate
                avg_win = tm.avg_win
                avg_loss = tm.avg_loss
                profit_factor = tm.safe_profit_factor
                expectancy = tm.win_rate * tm.avg_win + (1.0 - tm.win_rate) * tm.avg_loss
                trade_frequency = trade_count / bar_count if bar_count > 0 else None
        except (ValueError, ZeroDivisionError):
            pass

    return RegimeConditionedMetrics(
        bucket=bucket,
        bar_count=bar_count,
        exposure_fraction=exposure_fraction,
        total_return=ret_stats["total_return"],
        cagr=ret_stats["cagr"],
        sharpe=ret_stats["sharpe"],
        sortino=ret_stats["sortino"],
        volatility=ret_stats["volatility"],
        max_drawdown=ret_stats["max_drawdown"],
        trade_count=trade_count,
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        trade_frequency=trade_frequency,
        avg_bar_return=ret_stats["avg_bar_return"],
    )

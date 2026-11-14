"""
Shared helpers for the Monte Carlo stage tests.

The stage resamples a real equity curve, so these build SimulationRunResults
whose metrics are computed from the curve by the production metric functions,
not hand-set numbers on a mock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import uuid4

import numpy as np
import pandas as pd

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    stability_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    TradeMetrics,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

BARS_PER_SESSION = 78  # five-minute bars in a regular session


def weekdays(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def curve_with_sharpe(
    *,
    start: date,
    end: date,
    sharpe: float,
    seed: int,
    bar_vol: float = 1e-3,
) -> pd.DataFrame:
    """
    Five-minute equity curve over the weekdays in [start, end] whose realised
    full-window Sharpe (as risk_metrics computes it) equals `sharpe`.

    Noise is drawn from `seed`, then shifted so the realised mean gives exactly
    the requested Sharpe. Two curves with the same Sharpe and different seeds
    differ in which sessions were good, not in the headline number.
    """
    days = weekdays(start, end)
    n_returns = len(days) * BARS_PER_SESSION
    noise = np.random.default_rng(seed).normal(0.0, bar_vol, n_returns)
    noise -= noise.mean()
    returns = noise + sharpe * noise.std(ddof=1) / np.sqrt(BARS_PER_YEAR)

    # One opening point before the first bar, so every session has 78 returns.
    equity = 100_000.0 * np.concatenate([[1.0], np.cumprod(1.0 + returns)])
    session_open = pd.to_datetime([d.isoformat() for d in days], utc=True) + pd.Timedelta(
        hours=14, minutes=30
    )
    bar_offsets = pd.to_timedelta(np.arange(BARS_PER_SESSION) * 5, unit="min")
    timestamps = [session_open[0] - pd.Timedelta(minutes=5)]
    for opened in session_open:
        timestamps.extend(opened + bar_offsets)
    return pd.DataFrame({"timestamp": timestamps, "equity": equity})


def result_from_curve(
    request: SimulationRunRequest,
    curve: pd.DataFrame,
    *,
    trade_count: int = 40,
) -> SimulationRunResult:
    return SimulationRunResult(
        run_id=uuid4(),
        experiment_id=request.experiment_id,
        strategy_id=request.strategy_id,
        dataset_version=request.dataset_version,
        random_seed=request.random_seed,
        symbols=sorted(request.symbols),
        start_date=request.start_date,
        end_date=request.end_date,
        trade_count=trade_count,
        equity_points=len(curve),
        per_bar_metric_points=0,
        status="completed",
        return_metrics=return_metrics(curve),
        risk_metrics=risk_metrics(curve),
        trade_metrics=TradeMetrics(
            total_trades=trade_count,
            win_rate=0.6,
            avg_win=10.0,
            avg_loss=-5.0,
            profit_factor=1.5,
            largest_win=50.0,
            largest_loss=-20.0,
        ),
        stability_metrics=stability_metrics(curve),
        equity_curve=curve,
    )


class CurveRunner:
    """
    Stand-in for SimulationRunner: `curve_for(request)` supplies the equity
    curve, or None to simulate a run that produced nothing.
    """

    def __init__(
        self,
        curve_for: Callable[[SimulationRunRequest], pd.DataFrame | None],
        *,
        trade_count: int = 40,
    ) -> None:
        self._curve_for = curve_for
        self._trade_count = trade_count
        self.requests: list[SimulationRunRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult | None:
        self.requests.append(request)
        curve = self._curve_for(request)
        if curve is None:
            return None
        return result_from_curve(request, curve, trade_count=self._trade_count)


def sharpe_runner(
    sharpe_by_strategy: dict[str, float],
    *,
    trade_count: int = 40,
) -> CurveRunner:
    """Runner whose curve for each strategy has the given full-window Sharpe."""

    def curve_for(request: SimulationRunRequest) -> pd.DataFrame:
        return curve_with_sharpe(
            start=request.start_date,
            end=request.end_date,
            sharpe=sharpe_by_strategy[request.strategy_id],
            seed=sum(map(ord, request.strategy_id)),
        )

    return CurveRunner(curve_for, trade_count=trade_count)


def strategy(strategy_id: str = "strat-1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )

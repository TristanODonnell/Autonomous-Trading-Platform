"""
MonteCarloAggregator — turns resampled paths into a Monte Carlo verdict.

Responsibility
--------------
Given the per-path metrics of one strategy's session-block bootstrap (see
session_block_bootstrap.py), describe the distributions and decide whether the
strategy clears the Monte Carlo gate.

This module has zero awareness of the pipeline, stages, or simulation runner.

Pass verdict
------------
A strategy passes iff both hold:

    pass_rate >= min_pass_rate
    the base run's trade count >= filter_config.min_trades

where pass_rate = (paths that clear min_sharpe, min_total_return and
max_drawdown together) / (all paths). Each path is one alternate history of
the same length, so pass_rate estimates the probability that the strategy
clears the thresholds, given how much history there is.

Example: n_runs=2000, min_pass_rate=0.70 → at least 1,400 of the 2,000
resampled paths must clear every threshold.

min_trades is checked against the base run only: a resampled path is
reshuffled session returns and has no trades to count.

Why per-path filtering rather than filtering on aggregate means?
  Mean Sharpe of 1.2 could hide the fact that 40% of paths went badly negative.
  Per-path filtering surfaces that; aggregate-mean filtering masks it.

Missing evidence fails: a non-finite path metric counts as a failed path, and
a base run without a trade count does not pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig

from .session_block_bootstrap import BootstrapPaths

# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MetricDistribution:
    """
    Descriptive statistics for a single metric across the resampled paths.
    All values are in the natural unit of the metric (e.g. ratio, fraction).

    std depends mostly on how many sessions were resampled, not on the
    strategy, so compare it only between runs over the same window length.
    """

    mean: float
    median: float
    std: float
    min: float
    max: float
    p05: float
    p95: float


@dataclass
class MonteCarloAggregation:
    """
    Full aggregation result for a single strategy across its resampled paths.

    Fields
    ------
    strategy_id           : identifies the strategy these results belong to.
    n_runs                : number of resampled paths.
    n_passed              : paths whose metrics cleared every path threshold.
    pass_rate             : n_passed / n_runs  (0.0 – 1.0).
    passed                : True iff pass_rate >= min_pass_rate and the base
                            run has enough trades.
    sharpe_dist           : distribution of Sharpe ratio across paths.
    return_dist           : distribution of total return across paths.
    drawdown_dist         : distribution of max drawdown across paths
                            (values are negative, e.g. -0.15 = 15% drawdown).
    prob_positive_sharpe  : fraction of paths with Sharpe > 0. The stability
                            score the validation scorers read.
    prob_sharpe_above_min : fraction of paths with Sharpe >= min_sharpe.
                            Reported for comparison; it moves with the config.
    n_sessions            : sessions in the base run that were resampled.
    failure_reasons       : why the strategy or its paths failed (for logging).
    """

    strategy_id: str
    n_runs: int
    n_passed: int
    pass_rate: float
    passed: bool
    sharpe_dist: MetricDistribution
    return_dist: MetricDistribution
    drawdown_dist: MetricDistribution
    prob_positive_sharpe: float
    prob_sharpe_above_min: float
    n_sessions: int
    failure_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class MonteCarloAggregator:
    """
    Aggregates one strategy's resampled paths into a MonteCarloAggregation.

    Parameters
    ----------
    filter_config   : min_sharpe, min_total_return and max_drawdown are applied
                      to each path; min_trades to the base run.
    min_pass_rate   : fraction of paths that must pass for the strategy to
                      survive the stage (e.g. 0.70 = 70%).
    """

    def __init__(
        self,
        *,
        filter_config: FilterConfig,
        min_pass_rate: float,
    ) -> None:
        if not (0.0 < min_pass_rate <= 1.0):
            raise ValueError("min_pass_rate must be in (0, 1]")
        self._filter_config = filter_config
        self._min_pass_rate = min_pass_rate

    def aggregate(
        self,
        *,
        strategy_id: str,
        paths: BootstrapPaths,
        base_trade_count: int | None,
    ) -> MonteCarloAggregation:
        """
        Parameters
        ----------
        strategy_id      : used to populate the output dataclass.
        paths            : per-path metrics from session_block_bootstrap.
        base_trade_count : closed trades in the base run; None means unknown
                           and does not pass.
        """
        n_runs = paths.n_paths
        if n_runs == 0:
            raise ValueError(f"No resampled paths supplied for strategy '{strategy_id}'")

        fc = self._filter_config
        # "not (x >= threshold)" rather than "x < threshold": a NaN metric fails.
        path_checks = [
            (f"sharpe < {fc.min_sharpe}", ~(paths.sharpe >= fc.min_sharpe)),
            (
                f"total_return < {fc.min_total_return}",
                ~(paths.total_return >= fc.min_total_return),
            ),
            (f"max_drawdown < {fc.max_drawdown}", ~(paths.max_drawdown >= fc.max_drawdown)),
        ]
        path_failed = np.zeros(n_runs, dtype=bool)
        for _, failed in path_checks:
            path_failed |= failed

        n_passed = int(n_runs - path_failed.sum())
        pass_rate = n_passed / n_runs

        failure_reasons = [
            f"{label} on {share:.0%} of paths"
            for label, share in sorted(
                ((label, float(failed.mean())) for label, failed in path_checks),
                key=lambda item: -item[1],
            )
            if share > 0.0
        ]
        enough_trades = base_trade_count is not None and base_trade_count >= fc.min_trades
        if not enough_trades:
            shown = "unknown" if base_trade_count is None else str(base_trade_count)
            failure_reasons.insert(0, f"base run trades {shown} < {fc.min_trades}")

        return MonteCarloAggregation(
            strategy_id=strategy_id,
            n_runs=n_runs,
            n_passed=n_passed,
            pass_rate=pass_rate,
            passed=enough_trades and pass_rate >= self._min_pass_rate,
            sharpe_dist=self._distribution(paths.sharpe),
            return_dist=self._distribution(paths.total_return),
            drawdown_dist=self._distribution(paths.max_drawdown),
            prob_positive_sharpe=float((paths.sharpe > 0.0).mean()),
            prob_sharpe_above_min=float((paths.sharpe >= fc.min_sharpe).mean()),
            n_sessions=paths.n_sessions,
            failure_reasons=failure_reasons,
        )

    @staticmethod
    def _distribution(values: np.ndarray) -> MetricDistribution:
        p05, p95 = np.percentile(values, [5.0, 95.0])
        return MetricDistribution(
            mean=float(values.mean()),
            median=float(np.median(values)),
            std=float(values.std(ddof=1)) if len(values) > 1 else 0.0,
            min=float(values.min()),
            max=float(values.max()),
            p05=float(p05),
            p95=float(p95),
        )

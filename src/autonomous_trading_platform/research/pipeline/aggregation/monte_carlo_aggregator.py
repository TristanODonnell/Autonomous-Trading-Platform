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
    the base run clears every other threshold in filter_config

where pass_rate = (paths that clear min_sharpe, min_total_return and
max_drawdown together) / (all paths). Each path is one alternate history of
the same length, so pass_rate estimates the probability that the strategy
clears the thresholds, given how much history there is.

Example: n_runs=2000, min_pass_rate=0.70 → at least 1,400 of the 2,000
resampled paths must clear every threshold.

Everything else in filter_config (trade count, consistency, profit factor,
win rate, return variance, turnover, the per-window robustness checks) is
checked against the base run only: a resampled path is reshuffled session
returns, with no trades or calendar windows to measure. base_run_filter_config
gives the config for that check, so a threshold added to FilterConfig later
lands on the base run without a change here.

Why per-path filtering rather than filtering on aggregate means?
  Mean Sharpe of 1.2 could hide the fact that 40% of paths went badly negative.
  Per-path filtering surfaces that; aggregate-mean filtering masks it.

Missing evidence fails: a non-finite path metric counts as a failed path, and
a base run whose checks were not evaluated does not pass.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig

from .session_block_bootstrap import BootstrapPaths


def base_run_filter_config(filter_config: FilterConfig) -> FilterConfig:
    """
    filter_config with the three per-path thresholds switched off.

    Applying the single-run filter with the result to the base run checks
    exactly the thresholds a resampled path cannot measure.
    """
    return replace(
        filter_config,
        min_sharpe=-math.inf,
        min_total_return=-math.inf,
        max_drawdown=-math.inf,
    )


def _share_text(share: float) -> str:
    """A share as a whole percentage; one that is above zero and rounds to 0 reads "<1%"."""
    return "<1%" if 0.0 < share < 0.005 else f"{share:.0%}"


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
                            run clears its own checks.
    sharpe_dist           : distribution of Sharpe ratio across paths.
    return_dist           : distribution of total return across paths.
    drawdown_dist         : distribution of max drawdown across paths
                            (values are negative, e.g. -0.15 = 15% drawdown).
    prob_positive_sharpe  : fraction of paths with Sharpe > 0. The stability
                            score the validation scorers read.
    prob_sharpe_above_min : fraction of paths with Sharpe >= min_sharpe.
                            Reported for comparison; it moves with the config.
    n_sessions            : sessions in the base run that were resampled.
    failure_reasons       : base-run failures and the pass-rate shortfall
                            first (these decide the verdict), then the share
                            of paths failing each threshold (context, listed
                            even when the strategy passes).
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
                      to each path. The caller applies the rest to the base
                      run (base_run_filter_config) and passes what failed.
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
        base_run_failures: Sequence[str] | None,
    ) -> MonteCarloAggregation:
        """
        Parameters
        ----------
        strategy_id       : used to populate the output dataclass.
        paths             : per-path metrics from session_block_bootstrap.
        base_run_failures : what the base run failed among the thresholds a
                            path cannot measure; empty when it cleared them
                            all. None means the check was not run, and does
                            not pass.
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

        if base_run_failures is None:
            failure_reasons = ["base run: checks not evaluated"]
        else:
            failure_reasons = [f"base run: {reason}" for reason in base_run_failures]
        base_run_ok = not failure_reasons

        enough_paths = pass_rate >= self._min_pass_rate
        if not enough_paths:
            # Counts, not a rounded percentage: 1199/2000 would print as "60%".
            failure_reasons.append(
                f"{n_passed}/{n_runs} paths clear the thresholds, need {self._min_pass_rate:.0%}"
            )
        failure_reasons.extend(
            f"{label} on {_share_text(share)} of paths"
            for label, share in sorted(
                ((label, float(failed.mean())) for label, failed in path_checks),
                key=lambda item: -item[1],
            )
            if share > 0.0
        )

        return MonteCarloAggregation(
            strategy_id=strategy_id,
            n_runs=n_runs,
            n_passed=n_passed,
            pass_rate=pass_rate,
            passed=base_run_ok and enough_paths,
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

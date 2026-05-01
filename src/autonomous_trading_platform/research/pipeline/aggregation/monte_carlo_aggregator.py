"""
MonteCarloAggregator — pure statistics layer for Monte Carlo stage results.

Responsibility
--------------
Given N SimulationRunResults for a single strategy (each run with a different
random seed), compute aggregate statistics and determine whether the strategy
is structurally robust enough to survive the Monte Carlo gate.

This module has zero awareness of the pipeline, stages, or simulation runner.
It only knows about SimulationRunResult and the filter/scoring contracts.
That makes it fully unit-testable in isolation.

Aggregation logic
-----------------
For each key metric (Sharpe, total return, max drawdown) we record:
  - mean   : central tendency across N runs
  - median : robust central tendency (less sensitive to outlier runs)
  - std    : spread — low std means the strategy behaves consistently
  - min    : worst observed outcome across all runs
  - max    : best observed outcome across all runs

Pass verdict
------------
A strategy passes the Monte Carlo gate iff:

    pass_rate >= min_pass_rate

where pass_rate = (number of runs whose metrics clear filter_config thresholds)
                 / N

Example: n_runs=50, min_pass_rate=0.70 → strategy must survive at least 35/50
randomised runs.

Why per-run filtering rather than filtering on aggregate means?
  Mean Sharpe of 1.2 could hide the fact that 40% of runs went badly negative.
  Per-run filtering surfaces that fragility; aggregate-mean filtering masks it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunResult

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MetricDistribution:
    """
    Descriptive statistics for a single metric across N Monte Carlo runs.
    All values are in the natural unit of the metric (e.g. ratio, fraction).
    """

    mean: float
    median: float
    std: float
    min: float
    max: float

    @property
    def coefficient_of_variation(self) -> float:
        """
        Relative spread — std / |mean|.
        Useful for comparing stability across metrics with different scales.
        Returns 0.0 when mean is zero to avoid division-by-zero.
        """
        if self.mean == 0.0:
            return 0.0
        return self.std / abs(self.mean)


@dataclass
class MonteCarloAggregation:
    """
    Full aggregation result for a single strategy across N Monte Carlo runs.

    Fields
    ------
    strategy_id     : identifies the strategy these results belong to.
    n_runs          : total number of runs submitted (= len(run_results)).
    n_passed        : runs whose metrics cleared every filter threshold.
    pass_rate       : n_passed / n_runs  (0.0 – 1.0).
    passed          : True iff pass_rate >= min_pass_rate supplied to aggregator.
    sharpe_dist     : distribution of Sharpe ratio across all runs.
    return_dist     : distribution of total return across all runs.
    drawdown_dist   : distribution of max drawdown across all runs
                      (values are negative, e.g. -0.15 = 15% drawdown).
    run_results     : the raw SimulationRunResults that were aggregated.
                      Kept so the stage can forward them into StageResult.
    failure_reasons : summary of why runs failed (for logging / debugging).
    """

    strategy_id: str
    n_runs: int
    n_passed: int
    pass_rate: float
    passed: bool
    sharpe_dist: MetricDistribution
    return_dist: MetricDistribution
    drawdown_dist: MetricDistribution
    run_results: list[SimulationRunResult] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class MonteCarloAggregator:
    """
    Aggregates N SimulationRunResults for a single strategy into a
    MonteCarloAggregation verdict.

    Parameters
    ----------
    filter_config   : thresholds applied to each individual run to decide
                      whether that run "passed".
    min_pass_rate   : fraction of runs that must pass for the strategy to
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(
        self,
        strategy_id: str,
        run_results: list[SimulationRunResult],
    ) -> MonteCarloAggregation:
        """
        Aggregate N run results for one strategy.

        Parameters
        ----------
        strategy_id  : used to populate the output dataclass.
        run_results  : all N SimulationRunResults for this strategy.
                       Must be non-empty; raises ValueError otherwise.

        Returns
        -------
        MonteCarloAggregation with full statistics and a pass/fail verdict.
        """
        if not run_results:
            raise ValueError(f"No run results supplied for strategy '{strategy_id}'")

        sharpe_values = self._extract_sharpe(run_results)
        return_values = self._extract_total_return(run_results)
        drawdown_values = self._extract_max_drawdown(run_results)

        passed_runs, failure_reasons = self._evaluate_runs(run_results)
        n_passed = len(passed_runs)
        n_runs = len(run_results)
        pass_rate = n_passed / n_runs
        passed = pass_rate >= self._min_pass_rate

        return MonteCarloAggregation(
            strategy_id=strategy_id,
            n_runs=n_runs,
            n_passed=n_passed,
            pass_rate=pass_rate,
            passed=passed,
            sharpe_dist=self._distribution(sharpe_values),
            return_dist=self._distribution(return_values),
            drawdown_dist=self._distribution(drawdown_values),
            run_results=run_results,
            failure_reasons=failure_reasons,
        )

    # ------------------------------------------------------------------
    # Per-run evaluation
    # ------------------------------------------------------------------

    def _evaluate_runs(
        self,
        run_results: list[SimulationRunResult],
    ) -> tuple[list[SimulationRunResult], list[str]]:
        """
        Apply filter_config thresholds to each run independently.

        Returns
        -------
        (passed_runs, failure_summary)
        failure_summary is deduplicated for logging — not one entry per run.
        """
        passed: list[SimulationRunResult] = []
        failure_counts: dict[str, int] = {}

        fc = self._filter_config

        for result in run_results:
            run_failures: list[str] = []

            sharpe = self._safe_sharpe(result)
            total_return = self._safe_total_return(result)
            max_drawdown = self._safe_max_drawdown(result)
            trade_count = self._safe_trade_count(result)

            if sharpe < fc.min_sharpe:
                run_failures.append(f"sharpe {sharpe:.3f} < {fc.min_sharpe}")
            if total_return < fc.min_total_return:
                run_failures.append(f"total_return {total_return:.3f} < {fc.min_total_return}")
            if max_drawdown < fc.max_drawdown:
                run_failures.append(f"max_drawdown {max_drawdown:.3f} < {fc.max_drawdown}")
            if trade_count < fc.min_trades:
                run_failures.append(f"trades {trade_count} < {fc.min_trades}")

            if run_failures:
                for reason in run_failures:
                    failure_counts[reason] = failure_counts.get(reason, 0) + 1
            else:
                passed.append(result)

        # Build a human-readable summary sorted by frequency
        failure_summary = [
            f"{reason} (×{count})"
            for reason, count in sorted(failure_counts.items(), key=lambda x: -x[1])
        ]
        return passed, failure_summary

    # ------------------------------------------------------------------
    # Metric extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_sharpe(result: SimulationRunResult) -> float:
        if result.risk_metrics is None:
            return 0.0
        return result.risk_metrics.sharpe_ratio or 0.0

    @staticmethod
    def _safe_total_return(result: SimulationRunResult) -> float:
        if result.return_metrics is None:
            return 0.0
        return result.return_metrics.total_return or 0.0

    @staticmethod
    def _safe_max_drawdown(result: SimulationRunResult) -> float:
        if result.risk_metrics is None:
            return 0.0
        return result.risk_metrics.max_drawdown or 0.0

    @staticmethod
    def _safe_trade_count(result: SimulationRunResult) -> int:
        if result.trade_metrics is None:
            return 0
        return result.trade_metrics.total_trades or 0

    @staticmethod
    def _extract_sharpe(run_results: list[SimulationRunResult]) -> list[float]:
        return [MonteCarloAggregator._safe_sharpe(r) for r in run_results]

    @staticmethod
    def _extract_total_return(run_results: list[SimulationRunResult]) -> list[float]:
        return [MonteCarloAggregator._safe_total_return(r) for r in run_results]

    @staticmethod
    def _extract_max_drawdown(run_results: list[SimulationRunResult]) -> list[float]:
        return [MonteCarloAggregator._safe_max_drawdown(r) for r in run_results]

    # ------------------------------------------------------------------
    # Distribution builder
    # ------------------------------------------------------------------

    @staticmethod
    def _distribution(values: list[float]) -> MetricDistribution:
        if len(values) == 1:
            v = values[0]
            return MetricDistribution(mean=v, median=v, std=0.0, min=v, max=v)
        return MetricDistribution(
            mean=statistics.mean(values),
            median=statistics.median(values),
            std=statistics.stdev(values),
            min=min(values),
            max=max(values),
        )

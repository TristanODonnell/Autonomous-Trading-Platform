"""MonteCarloAggregator: resampled paths in, distribution and verdict out."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence

import numpy as np
import pytest

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig
from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MonteCarloAggregator,
    base_run_filter_config,
)
from autonomous_trading_platform.research.pipeline.aggregation.session_block_bootstrap import (
    BootstrapPaths,
)

FILTER = FilterConfig(min_sharpe=1.0, max_drawdown=-0.20, min_trades=10, min_total_return=0.0)


def _paths(
    sharpe: list[float],
    total_return: list[float] | None = None,
    max_drawdown: list[float] | None = None,
) -> BootstrapPaths:
    n = len(sharpe)
    return BootstrapPaths(
        sharpe=np.array(sharpe, dtype=np.float64),
        total_return=np.array(total_return or [0.05] * n, dtype=np.float64),
        max_drawdown=np.array(max_drawdown or [-0.05] * n, dtype=np.float64),
        n_sessions=63,
    )


def _aggregate(
    paths: BootstrapPaths,
    *,
    min_pass_rate: float = 0.70,
    base_run_failures: Sequence[str] | None = (),
):
    return MonteCarloAggregator(filter_config=FILTER, min_pass_rate=min_pass_rate).aggregate(
        strategy_id="s1", paths=paths, base_run_failures=base_run_failures
    )


def test_pass_rate_is_the_share_of_paths_clearing_every_threshold() -> None:
    # 10 paths: 2 fail Sharpe, 1 fails drawdown, 1 fails return, 1 fails two at once.
    paths = _paths(
        sharpe=[2.0, 2.0, 2.0, 2.0, 2.0, 0.5, 0.2, 2.0, 2.0, 0.9],
        total_return=[0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, -0.01, -0.02],
        max_drawdown=[-0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.1, -0.3, -0.1, -0.1],
    )
    agg = _aggregate(paths, min_pass_rate=0.5)

    assert agg.n_runs == 10
    assert agg.n_passed == 5
    assert agg.pass_rate == pytest.approx(0.5)
    assert agg.passed  # exactly at the threshold passes
    assert agg.failure_reasons == [
        "sharpe < 1.0 on 30% of paths",
        "total_return < 0.0 on 20% of paths",
        "max_drawdown < -0.2 on 10% of paths",
    ]

    short = _aggregate(paths, min_pass_rate=0.51)
    assert not short.passed
    # The reason for the verdict comes first, then the per-threshold shares.
    assert short.failure_reasons == [
        "5/10 paths clear the thresholds, need 51%",
        *agg.failure_reasons,
    ]


def test_near_miss_reason_gives_counts_not_a_rounded_share() -> None:
    # 1199 of 2000 is 59.95%: as a percentage it would read "60%, need 60%".
    paths = _paths([2.0] * 1199 + [0.5] * 801)
    agg = _aggregate(paths, min_pass_rate=0.60)

    assert not agg.passed
    assert agg.failure_reasons == [
        "1199/2000 paths clear the thresholds, need 60%",
        "sharpe < 1.0 on 40% of paths",
    ]


def test_distribution_statistics_and_tracked_probabilities() -> None:
    sharpe = np.linspace(-1.0, 3.0, 401)  # evenly spread, 100 values <= 0
    agg = _aggregate(_paths(sharpe.tolist()))

    assert agg.sharpe_dist.mean == pytest.approx(1.0)
    assert agg.sharpe_dist.median == pytest.approx(1.0)
    assert agg.sharpe_dist.std == pytest.approx(float(sharpe.std(ddof=1)))
    assert (agg.sharpe_dist.min, agg.sharpe_dist.max) == (-1.0, 3.0)
    assert agg.sharpe_dist.p05 == pytest.approx(-0.8)
    assert agg.sharpe_dist.p95 == pytest.approx(2.8)
    assert agg.prob_positive_sharpe == pytest.approx(300 / 401)
    assert agg.prob_sharpe_above_min == pytest.approx(201 / 401)  # Sharpe >= 1.0
    assert agg.n_sessions == 63


def test_a_share_that_rounds_to_zero_is_not_printed_as_zero() -> None:
    # 1 path in 400 misses the return threshold: 0.25%.
    paths = _paths([2.0] * 400, total_return=[0.05] * 399 + [-0.01])
    agg = _aggregate(paths)

    assert agg.passed
    assert agg.failure_reasons == ["total_return < 0.0 on <1% of paths"]


def test_a_base_run_failure_fails_the_strategy_whatever_the_pass_rate() -> None:
    all_paths_pass = _paths([2.0] * 20)

    clean = _aggregate(all_paths_pass, base_run_failures=[])
    failed = _aggregate(
        all_paths_pass,
        base_run_failures=[
            "total_trades 9 < min_trades 10",
            "daily_turnover 13.80x > max_daily_turnover 10.0x",
        ],
    )

    assert clean.passed and clean.failure_reasons == []
    assert failed.pass_rate == 1.0
    assert not failed.passed
    assert failed.failure_reasons == [
        "base run: total_trades 9 < min_trades 10",
        "base run: daily_turnover 13.80x > max_daily_turnover 10.0x",
    ]


def test_base_run_and_path_reasons_are_both_reported() -> None:
    agg = _aggregate(
        _paths([2.0] * 5 + [0.5] * 5),
        base_run_failures=["total_trades 9 < min_trades 10"],
    )
    assert agg.failure_reasons == [
        "base run: total_trades 9 < min_trades 10",
        "5/10 paths clear the thresholds, need 70%",
        "sharpe < 1.0 on 50% of paths",
    ]


def test_unevaluated_base_run_does_not_pass() -> None:
    agg = _aggregate(_paths([2.0] * 20), base_run_failures=None)
    assert not agg.passed
    assert agg.failure_reasons == ["base run: checks not evaluated"]


def test_base_run_filter_config_switches_off_only_the_per_path_thresholds() -> None:
    full = FilterConfig(
        min_sharpe=1.0,
        max_drawdown=-0.20,
        min_trades=10,
        min_consistency_score=0.3,
        min_profit_factor=1.2,
        min_win_rate=0.4,
        max_return_variance=0.01,
        min_total_return=0.0,
        robustness_min_sharpe=0.5,
        robustness_min_profitable_windows=2,
        max_daily_turnover=10.0,
    )
    base_run = base_run_filter_config(full)

    per_path = {"min_sharpe", "min_total_return", "max_drawdown"}
    changed = {
        f.name
        for f in dataclasses.fields(FilterConfig)
        if getattr(base_run, f.name) != getattr(full, f.name)
    }
    assert changed == per_path
    assert all(getattr(base_run, name) == -math.inf for name in per_path)


def test_non_finite_path_metric_counts_as_a_failed_path() -> None:
    paths = _paths(
        sharpe=[2.0, float("nan"), 2.0, 2.0],
        max_drawdown=[-0.05, -0.05, float("nan"), -0.05],
    )
    agg = _aggregate(paths, min_pass_rate=0.75)
    assert agg.n_passed == 2
    assert not agg.passed


def test_no_paths_raises() -> None:
    with pytest.raises(ValueError, match="No resampled paths"):
        _aggregate(_paths([]))


@pytest.mark.parametrize("bad", [0.0, 1.01, -0.1])
def test_min_pass_rate_must_be_a_positive_fraction(bad: float) -> None:
    with pytest.raises(ValueError, match="min_pass_rate"):
        MonteCarloAggregator(filter_config=FILTER, min_pass_rate=bad)

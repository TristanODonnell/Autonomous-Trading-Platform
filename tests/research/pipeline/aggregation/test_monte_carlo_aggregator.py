"""MonteCarloAggregator: resampled paths in, distribution and verdict out."""

from __future__ import annotations

import numpy as np
import pytest

from autonomous_trading_platform.research.experiments.filtering.config import FilterConfig
from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MonteCarloAggregator,
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


def _aggregate(paths: BootstrapPaths, *, min_pass_rate: float = 0.70, trades: int | None = 40):
    return MonteCarloAggregator(filter_config=FILTER, min_pass_rate=min_pass_rate).aggregate(
        strategy_id="s1", paths=paths, base_trade_count=trades
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
    assert not _aggregate(paths, min_pass_rate=0.51).passed


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


def test_min_trades_applies_to_the_base_run_only() -> None:
    all_paths_pass = _paths([2.0] * 20)

    enough = _aggregate(all_paths_pass, trades=10)
    too_few = _aggregate(all_paths_pass, trades=9)

    assert enough.passed and enough.failure_reasons == []
    assert too_few.pass_rate == 1.0
    assert not too_few.passed
    assert too_few.failure_reasons == ["base run trades 9 < 10"]


def test_unknown_trade_count_does_not_pass() -> None:
    agg = _aggregate(_paths([2.0] * 20), trades=None)
    assert not agg.passed
    assert agg.failure_reasons == ["base run trades unknown < 10"]


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

"""
Session-block bootstrap: the resampling core behind the Monte Carlo stage.

The anchor test is the identity path: sessions in their original order must
reproduce the production metric functions exactly, which pins this module's
Sharpe, return and drawdown formulas to risk_metrics.py / return_metrics.py.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.common.annualisation import BARS_PER_YEAR
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    total_return,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics,
)
from autonomous_trading_platform.research.pipeline.aggregation.session_block_bootstrap import (
    evaluate_session_paths,
    session_block_bootstrap,
    session_returns,
)
from tests.research.pipeline.monte_carlo_support import BARS_PER_SESSION, curve_with_sharpe

START, END = date(2024, 1, 2), date(2024, 3, 28)  # 63 weekdays


def _ragged_curve(seed: int = 7) -> pd.DataFrame:
    """63 sessions; every ninth is an early close with 42 bars instead of 78."""
    rng = np.random.default_rng(seed)
    rows, equity = [], 100_000.0
    for i, day in enumerate(pd.bdate_range("2024-01-02", periods=63, tz="UTC")):
        for j in range(78 if i % 9 else 42):
            equity *= 1.0 + rng.normal(2e-5, 1.1e-3)
            rows.append(
                {"timestamp": day + pd.Timedelta(hours=14, minutes=30 + 5 * j), "equity": equity}
            )
    return pd.DataFrame(rows)


class TestSessionReturns:
    def test_groups_bar_returns_by_session(self) -> None:
        sessions = session_returns(curve_with_sharpe(start=START, end=END, sharpe=1.0, seed=1))
        assert sessions.n_sessions == 63
        assert set(sessions.bar_counts.tolist()) == {BARS_PER_SESSION}

    def test_ragged_sessions_are_zero_padded(self) -> None:
        sessions = session_returns(_ragged_curve())
        # The first session loses one return: its first bar has no previous bar.
        assert sorted(set(sessions.bar_counts.tolist())) == [41, 42, 78]
        short = sessions.bar_counts < 78
        for row, count in zip(sessions.padded[short], sessions.bar_counts[short], strict=True):
            assert np.all(row[count:] == 0.0)

    def test_row_order_does_not_matter(self) -> None:
        curve = _ragged_curve()
        shuffled = curve.sample(frac=1.0, random_state=3)
        np.testing.assert_array_equal(
            session_returns(curve).padded, session_returns(shuffled).padded
        )

    @pytest.mark.parametrize(
        "curve, message",
        [
            (pd.DataFrame(columns=["timestamp", "equity"]), "empty"),
            (pd.DataFrame({"timestamp": [pd.Timestamp("2024-01-02", tz="UTC")]}), "missing"),
            (
                pd.DataFrame(
                    {"timestamp": [pd.Timestamp("2024-01-02", tz="UTC")], "equity": [1.0]}
                ),
                "fewer than 2",
            ),
        ],
    )
    def test_unusable_curves_raise(self, curve: pd.DataFrame, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            session_returns(curve)

    @pytest.mark.parametrize("bad_equity", [0.0, -5.0, float("nan")])
    def test_non_positive_or_non_finite_equity_raises(self, bad_equity: float) -> None:
        curve = _ragged_curve()
        curve.loc[100, "equity"] = bad_equity
        with pytest.raises(ValueError, match="non-finite or non-positive"):
            session_returns(curve)


class TestIdentityPath:
    @pytest.mark.parametrize("risk_free_rate", [0.0, 0.04])
    def test_original_order_reproduces_production_metrics(self, risk_free_rate: float) -> None:
        curve = _ragged_curve()
        sessions = session_returns(curve)
        identity = np.arange(sessions.n_sessions)[None, :]

        paths = evaluate_session_paths(
            sessions, identity, bars_per_year=BARS_PER_YEAR, risk_free_rate=risk_free_rate
        )
        expected = risk_metrics(curve, risk_free_rate=risk_free_rate)

        assert paths.sharpe[0] == pytest.approx(expected.sharpe_ratio, rel=1e-9)
        assert paths.max_drawdown[0] == pytest.approx(expected.max_drawdown, rel=1e-9)
        assert paths.total_return[0] == pytest.approx(total_return(curve), rel=1e-9)

    def test_reordering_sessions_moves_drawdown_but_not_sharpe_or_return(self) -> None:
        """Why the stage resamples with replacement and does not just shuffle."""
        sessions = session_returns(_ragged_curve())
        rng = np.random.default_rng(0)
        shuffles = np.stack([rng.permutation(sessions.n_sessions) for _ in range(200)])

        paths = evaluate_session_paths(sessions, shuffles, bars_per_year=BARS_PER_YEAR)

        assert np.ptp(paths.sharpe) < 1e-9
        assert np.ptp(paths.total_return) < 1e-9
        assert np.ptp(paths.max_drawdown) > 0.005

    def test_flat_curve_has_zero_sharpe_and_no_drawdown(self) -> None:
        curve = _ragged_curve()
        curve["equity"] = 100_000.0
        paths = session_block_bootstrap(curve, n_paths=50, seed=1, bars_per_year=BARS_PER_YEAR)
        assert np.all(paths.sharpe == 0.0)
        assert np.all(paths.max_drawdown == 0.0)
        assert np.all(paths.total_return == 0.0)


class TestBootstrap:
    def test_resampling_produces_a_real_distribution(self) -> None:
        curve = curve_with_sharpe(start=START, end=END, sharpe=2.0, seed=5)
        paths = session_block_bootstrap(curve, n_paths=2000, seed=11, bars_per_year=BARS_PER_YEAR)

        assert paths.n_paths == 2000
        assert paths.n_sessions == 63
        # 63 sessions is a quarter of a year: the Sharpe estimate is very uncertain.
        assert 1.5 < paths.sharpe.std(ddof=1) < 2.6
        # Centred near the observed Sharpe, with both tails present.
        assert paths.sharpe.mean() == pytest.approx(2.0, abs=0.3)
        assert (paths.sharpe < 0).any() and (paths.sharpe > 4).any()
        assert np.all(paths.max_drawdown <= 0.0)

    def test_longer_history_narrows_the_spread(self) -> None:
        short = curve_with_sharpe(start=START, end=END, sharpe=2.0, seed=5)
        long = curve_with_sharpe(start=START, end=date(2024, 12, 31), sharpe=2.0, seed=5)

        def spread(curve: pd.DataFrame) -> float:
            paths = session_block_bootstrap(
                curve, n_paths=1000, seed=3, bars_per_year=BARS_PER_YEAR
            )
            return float(paths.sharpe.std(ddof=1))

        # Four times the sessions → about half the spread (1 / sqrt(4)).
        assert spread(long) == pytest.approx(spread(short) / 2.0, rel=0.2)

    def test_same_seed_same_result_different_seed_different_result(self) -> None:
        curve = _ragged_curve()
        kwargs = {"n_paths": 300, "bars_per_year": BARS_PER_YEAR}
        first = session_block_bootstrap(curve, seed=11, **kwargs)
        again = session_block_bootstrap(curve, seed=11, **kwargs)
        other = session_block_bootstrap(curve, seed=12, **kwargs)

        np.testing.assert_array_equal(first.sharpe, again.sharpe)
        np.testing.assert_array_equal(first.max_drawdown, again.max_drawdown)
        assert not np.array_equal(first.sharpe, other.sharpe)

    def test_chunk_size_changes_memory_not_results(self) -> None:
        curve = _ragged_curve()
        kwargs = {"n_paths": 300, "seed": 11, "bars_per_year": BARS_PER_YEAR}
        whole = session_block_bootstrap(curve, **kwargs)
        one_path_at_a_time = session_block_bootstrap(curve, chunk_bytes=1, **kwargs)

        np.testing.assert_array_equal(whole.sharpe, one_path_at_a_time.sharpe)
        np.testing.assert_array_equal(whole.total_return, one_path_at_a_time.total_return)
        np.testing.assert_array_equal(whole.max_drawdown, one_path_at_a_time.max_drawdown)

    def test_single_session_cannot_be_resampled(self) -> None:
        curve = curve_with_sharpe(start=START, end=START, sharpe=1.0, seed=1)
        with pytest.raises(ValueError, match="at least 2"):
            session_block_bootstrap(curve, n_paths=10, seed=1, bars_per_year=BARS_PER_YEAR)

    def test_n_paths_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="n_paths"):
            session_block_bootstrap(_ragged_curve(), n_paths=0, seed=1, bars_per_year=BARS_PER_YEAR)

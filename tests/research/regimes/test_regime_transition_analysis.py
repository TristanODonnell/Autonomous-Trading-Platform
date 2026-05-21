from __future__ import annotations

import numpy as np
import pandas as pd

from autonomous_trading_platform.research.analysis.regimes.regime_transition_analysis import (
    RegimeTransitionAnalyzer,
)


def _make_equity_with_regime(
    regimes: list[str],
    base_return: float = 0.001,
) -> pd.DataFrame:
    n = len(regimes)
    equity = [100_000.0]
    for _ in range(n - 1):
        equity.append(equity[-1] * (1 + base_return))

    timestamps = pd.date_range("2023-01-03", periods=n, freq="B", tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "equity": equity,
            "regime_trend": regimes,
        }
    )


def _make_bar_returns_with_regime(
    equity_with_regime: pd.DataFrame,
) -> pd.DataFrame:
    sorted_eq = equity_with_regime.sort_values("timestamp").reset_index(drop=True)
    equities = sorted_eq["equity"].to_numpy(dtype=np.float64)
    bar_returns = np.diff(equities) / equities[:-1]

    regime_cols = [c for c in sorted_eq.columns if c.startswith("regime_")]
    result = sorted_eq.iloc[1:][["timestamp"] + regime_cols].copy().reset_index(drop=True)
    result["bar_return"] = bar_returns
    return result


class TestRegimeTransitionAnalyzer:
    def test_one_transition_detected(self) -> None:
        # 5 bull bars then 5 bear bars → one transition
        regimes = ["bull"] * 5 + ["bear"] * 5
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
            window_bars=3,
        )

        assert result.transition_count == 1
        assert result.transitions[0].from_regime == "bull"
        assert result.transitions[0].to_regime == "bear"

    def test_multiple_transitions_detected(self) -> None:
        regimes = ["bull"] * 4 + ["bear"] * 4 + ["sideways"] * 4
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
            window_bars=2,
        )

        assert result.transition_count == 2

    def test_no_transitions_when_single_regime(self) -> None:
        regimes = ["bull"] * 10
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
        )

        assert result.transition_count == 0
        assert result.transition_matrix == {}

    def test_transition_matrix_correct(self) -> None:
        regimes = ["bull"] * 3 + ["bear"] * 3 + ["bull"] * 3
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
        )

        assert result.transition_count == 2
        assert result.transition_matrix["bull"]["bear"] == 1
        assert result.transition_matrix["bear"]["bull"] == 1

    def test_duration_stats_populated(self) -> None:
        regimes = ["bull"] * 6 + ["bear"] * 4
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
        )

        assert "bull" in result.duration_stats
        bull_stats = result.duration_stats["bull"]
        assert bull_stats.mean_duration == 6.0
        assert bull_stats.episode_count == 1

    def test_no_column_returns_empty_result(self) -> None:
        equity = pd.DataFrame(
            {
                "timestamp": pd.date_range("2023-01-03", periods=5, freq="B", tz="UTC"),
                "equity": [100_000.0 * (1.001**i) for i in range(5)],
                # no regime_trend column
            }
        )
        bar_returns = pd.DataFrame(
            {
                "timestamp": pd.date_range("2023-01-04", periods=4, freq="B", tz="UTC"),
                "bar_return": [0.001] * 4,
            }
        )

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
        )

        assert result.transition_count == 0
        assert result.transitions == []

    def test_pre_post_returns_computed(self) -> None:
        # positive returns in bull, negative in bear
        n_bull = 10
        n_bear = 10
        bull_returns = [0.002] * n_bull
        bear_returns = [-0.002] * n_bear
        all_returns = bull_returns + bear_returns
        regimes = ["bull"] * n_bull + ["bear"] * n_bear

        equity = [100_000.0]
        for r in all_returns:
            equity.append(equity[-1] * (1 + r))

        timestamps = pd.date_range("2023-01-03", periods=n_bull + n_bear + 1, freq="B", tz="UTC")
        equity_df = pd.DataFrame(
            {
                "timestamp": timestamps[: n_bull + n_bear],
                "equity": equity[: n_bull + n_bear],
                "regime_trend": regimes,
            }
        )
        br_df = pd.DataFrame(
            {
                "timestamp": timestamps[1 : n_bull + n_bear],
                "bar_return": all_returns[: n_bull + n_bear - 1],
                "regime_trend": regimes[1:],
            }
        )

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity_df,
            bar_returns_with_regime=br_df,
            dimension="trend",
            window_bars=5,
        )

        assert result.transition_count == 1
        tw = result.transition_windows[0]
        # Pre-transition returns should be positive (bull regime)
        if tw.pre_return is not None:
            assert tw.pre_return > 0.0
        # Post-transition returns should be negative (bear regime)
        if tw.post_return is not None:
            assert tw.post_return < 0.0

    def test_avg_pre_post_returns_are_finite(self) -> None:
        regimes = ["bull"] * 5 + ["bear"] * 5 + ["sideways"] * 5
        equity = _make_equity_with_regime(regimes)
        bar_returns = _make_bar_returns_with_regime(equity)

        analyzer = RegimeTransitionAnalyzer()
        result = analyzer.analyze(
            equity_curve_with_regime=equity,
            bar_returns_with_regime=bar_returns,
            dimension="trend",
            window_bars=3,
        )

        if result.avg_pre_transition_return is not None:
            assert np.isfinite(result.avg_pre_transition_return)
        if result.avg_post_transition_return is not None:
            assert np.isfinite(result.avg_post_transition_return)

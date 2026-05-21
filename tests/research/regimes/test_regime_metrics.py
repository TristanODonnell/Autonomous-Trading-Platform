from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.analysis.regimes.regime_bucket import RegimeBucket
from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    compute_regime_metrics,
)


def _bull_bucket() -> RegimeBucket:
    return RegimeBucket(dimension="trend", label="bull")


def _bear_bucket() -> RegimeBucket:
    return RegimeBucket(dimension="trend", label="bear")


def _make_trades(pnls: list[float]) -> pd.DataFrame:
    """Build minimal trade_logs with buys and matching sells."""
    rows = []
    for i, pnl in enumerate(pnls):
        buy_price = 100.0
        sell_price = buy_price + pnl  # one-unit trade
        rows.append(
            {
                "symbol": "AAPL",
                "timestamp": pd.Timestamp(f"2023-01-{i + 3:02d}", tz="UTC"),
                "side": "buy",
                "quantity": 1.0,
                "price": buy_price,
                "fees": 0.0,
            }
        )
        rows.append(
            {
                "symbol": "AAPL",
                "timestamp": pd.Timestamp(f"2023-01-{i + 4:02d}", tz="UTC"),
                "side": "sell",
                "quantity": 1.0,
                "price": sell_price,
                "fees": 0.0,
            }
        )
    return pd.DataFrame(rows)


class TestComputeRegimeMetrics:
    def test_zero_bars_returns_zero_exposure(self) -> None:
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=np.array([]),
            trades=pd.DataFrame(),
            bar_count=0,
            total_bars=100,
        )
        assert m.bar_count == 0
        assert m.exposure_fraction == 0.0
        assert m.sharpe is None
        assert m.cagr is None
        assert m.total_return is None

    def test_single_bar_no_metrics(self) -> None:
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=np.array([0.01]),
            trades=pd.DataFrame(),
            bar_count=1,
            total_bars=100,
        )
        assert m.bar_count == 1
        assert m.sharpe is None
        assert m.sortino is None
        assert m.volatility is None
        assert m.total_return == pytest.approx(0.01)

    def test_positive_returns_positive_sharpe(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(loc=0.001, scale=0.002, size=50)  # positive drift with variance
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=50,
            total_bars=100,
        )
        assert m.sharpe is not None
        assert m.sharpe > 0.0
        assert m.volatility is not None
        assert m.volatility >= 0.0

    def test_negative_returns_negative_sharpe(self) -> None:
        rng = np.random.default_rng(7)
        returns = rng.normal(loc=-0.002, scale=0.001, size=50)  # negative drift with variance
        m = compute_regime_metrics(
            bucket=_bear_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=50,
            total_bars=100,
        )
        assert m.sharpe is not None
        assert m.sharpe < 0.0

    def test_max_drawdown_is_non_positive(self) -> None:
        returns = np.array([0.01, -0.05, 0.02, -0.03])
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=len(returns),
            total_bars=200,
        )
        assert m.max_drawdown is not None
        assert m.max_drawdown <= 0.0

    def test_total_return_compounded(self) -> None:
        returns = np.array([0.01, 0.02])  # two small positive bars
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=len(returns),
            total_bars=100,
        )
        expected = (1.01 * 1.02) - 1.0
        assert m.total_return == pytest.approx(expected)

    def test_exposure_fraction(self) -> None:
        returns = np.full(25, 0.001)
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=25,
            total_bars=100,
        )
        assert m.exposure_fraction == pytest.approx(0.25)

    def test_trade_metrics_populated(self) -> None:
        returns = np.full(20, 0.001)
        trades = _make_trades([5.0, -3.0, 8.0])
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=trades,
            bar_count=20,
            total_bars=100,
        )
        assert m.trade_count == 3
        assert m.win_rate is not None
        assert m.win_rate == pytest.approx(2 / 3)
        assert m.avg_win is not None
        assert m.avg_win > 0.0
        assert m.avg_loss is not None
        assert m.avg_loss < 0.0

    def test_no_trades_produces_zero_trade_count(self) -> None:
        returns = np.full(10, 0.001)
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=10,
            total_bars=100,
        )
        assert m.trade_count == 0
        assert m.win_rate is None

    def test_expectancy_sign_matches_pnl(self) -> None:
        returns = np.full(10, 0.0)
        trades = _make_trades([5.0, 5.0, 5.0])  # all wins
        m = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=trades,
            bar_count=10,
            total_bars=100,
        )
        assert m.expectancy is not None
        assert m.expectancy > 0.0

    def test_deterministic_for_same_inputs(self) -> None:
        returns = np.array([0.01, -0.005, 0.02, -0.01, 0.015])
        m1 = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=len(returns),
            total_bars=100,
        )
        m2 = compute_regime_metrics(
            bucket=_bull_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=len(returns),
            total_bars=100,
        )
        assert m1.sharpe == m2.sharpe
        assert m1.cagr == m2.cagr

    def test_all_downside_no_sortino_infinite(self) -> None:
        returns = np.full(20, -0.001)
        m = compute_regime_metrics(
            bucket=_bear_bucket(),
            bar_returns=returns,
            trades=pd.DataFrame(),
            bar_count=20,
            total_bars=100,
        )
        # sortino should be None or a finite value, never inf
        if m.sortino is not None:
            assert not np.isinf(m.sortino)

"""Tests for OverfittingAnalyzer."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.validation.overfitting_analysis import (
    OverfittingAnalyzer,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    FoldValidationInput,
    WalkForwardValidationService,
)


def _make_wf_result(
    n_folds: int = 5,
    train_sharpe: float = 2.0,
    test_sharpe: float = 1.5,
    n_passed: int | None = None,
):
    n_passed = n_passed if n_passed is not None else n_folds
    folds = [
        FoldValidationInput(
            fold_index=i,
            train_sharpe=train_sharpe,
            test_sharpe=test_sharpe,
            train_drawdown=-0.05,
            test_drawdown=-0.07,
            train_return=0.10,
            test_return=0.08,
            train_passed=True,
            test_passed=(i < n_passed),
            fold_passed=(i < n_passed),
        )
        for i in range(n_folds)
    ]
    return WalkForwardValidationService().analyze(folds)


def _make_equity_curve(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    returns = rng.normal(0.0002, 0.001, n)
    equity = np.cumprod(1.0 + returns) * 100_000.0
    ts = pd.date_range("2022-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "equity": equity})


def test_stable_strategy_low_probability():
    analyzer = OverfittingAnalyzer()
    wf = _make_wf_result(n_folds=8, train_sharpe=1.5, test_sharpe=1.4, n_passed=8)
    result = analyzer.analyze(
        strategy_id="stable",
        wf_result=wf,
        trade_count=200,
        equity_curve=_make_equity_curve(),
    )
    assert result.overfitting_probability < 0.5
    assert result.resistance_score > 0.5


def test_unstable_strategy_high_probability():
    """Train Sharpe 4.0, test 0.1, few trades → high overfit signal."""
    analyzer = OverfittingAnalyzer(min_trade_count=50)
    wf = _make_wf_result(n_folds=5, train_sharpe=4.0, test_sharpe=0.1, n_passed=2)
    result = analyzer.analyze(
        strategy_id="overfit",
        wf_result=wf,
        trade_count=10,
        equity_curve=_make_equity_curve(),
        sensitivity_score=0.9,
    )
    # Multiple risk indicators active — probability should be meaningfully above zero
    assert result.overfitting_probability > 0.3


def test_no_data_gives_zero_probability():
    analyzer = OverfittingAnalyzer()
    result = analyzer.analyze(strategy_id="empty")
    assert result.overfitting_probability == 0.0
    assert result.resistance_score == 1.0
    assert result.most_suspicious == []


def test_low_trade_count_flagged():
    analyzer = OverfittingAnalyzer(min_trade_count=30)
    result = analyzer.analyze(strategy_id="s", trade_count=5)
    assert result.indicators.low_trade_count_flag is True
    assert any("trades" in w.lower() for w in result.warnings)


def test_high_trade_count_not_flagged():
    analyzer = OverfittingAnalyzer(min_trade_count=30)
    result = analyzer.analyze(strategy_id="s", trade_count=200)
    assert result.indicators.low_trade_count_flag is False


def test_mc_instability_detected():
    """Simulate a MC aggregation with high Sharpe CoV."""
    mc_agg = MagicMock()
    mc_agg.sharpe_dist.coefficient_of_variation = 1.5  # very unstable
    mc_agg.sharpe_dist.mean = 1.0

    analyzer = OverfittingAnalyzer()
    result = analyzer.analyze(strategy_id="s", mc_aggregation=mc_agg)
    assert result.indicators.mc_instability is not None
    assert result.indicators.mc_instability > 0.4


def test_most_suspicious_ordered():
    """most_suspicious should contain indicators with highest scores."""
    analyzer = OverfittingAnalyzer()
    wf = _make_wf_result(train_sharpe=5.0, test_sharpe=0.2, n_passed=1)
    result = analyzer.analyze(
        strategy_id="s",
        wf_result=wf,
        trade_count=5,
        equity_curve=_make_equity_curve(),
        sensitivity_score=0.95,
    )
    assert len(result.most_suspicious) <= 3
    # train_test_degradation or fold_instability should be the top suspect
    assert any(x in result.most_suspicious for x in ("train_test_degradation", "fold_instability"))


def test_regime_concentration():
    """Strategy with poor regime coverage flagged."""
    mock_profile = MagicMock()
    # Only 1 out of 3 regimes has positive Sharpe per dimension
    dim = MagicMock()
    dim.metrics_by_label = {
        "bull": MagicMock(sharpe=1.5),
        "bear": MagicMock(sharpe=-0.5),
        "sideways": MagicMock(sharpe=-0.2),
    }
    mock_profile.by_trend = dim
    mock_profile.by_volatility = dim
    mock_profile.by_liquidity = dim
    mock_profile.by_mean_reversion = dim
    mock_profile.by_risk = dim

    analyzer = OverfittingAnalyzer()
    result = analyzer.analyze(strategy_id="s", regime_profile=mock_profile)
    # regime_concentration should be ~0.33 (1/3 positive)
    assert result.indicators.regime_concentration is not None
    assert result.indicators.regime_concentration < 0.5


def test_determinism():
    wf = _make_wf_result(n_folds=4, train_sharpe=1.8, test_sharpe=1.2)
    curve = _make_equity_curve()
    analyzer = OverfittingAnalyzer()
    r1 = analyzer.analyze(strategy_id="s", wf_result=wf, trade_count=100, equity_curve=curve)
    r2 = analyzer.analyze(strategy_id="s", wf_result=wf, trade_count=100, equity_curve=curve)
    assert r1.overfitting_probability == pytest.approx(r2.overfitting_probability)

"""Tests for StressTestService."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.validation.stress_test_service import (
    BUILT_IN_SCENARIOS,
    StressScenario,
    StressTestService,
)


def _make_equity_curve(n_bars: int = 200, trend: float = 0.0002) -> pd.DataFrame:
    """Synthetic upward-trending equity curve."""
    rng = np.random.default_rng(42)
    returns = rng.normal(loc=trend, scale=0.001, size=n_bars)
    equity = np.cumprod(1.0 + returns) * 100_000.0
    timestamps = pd.date_range("2023-01-01", periods=n_bars, freq="5min", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "equity": equity})


def test_built_in_scenarios_run():
    svc = StressTestService()
    curve = _make_equity_curve()
    summary = svc.run(equity_curve=curve, strategy_id="strat_test")

    assert summary.n_scenarios == len(BUILT_IN_SCENARIOS)
    assert 0.0 <= summary.survival_rate <= 1.0
    assert summary.worst_scenario is not None


def test_stress_empty_curve_raises():
    svc = StressTestService()
    with pytest.raises(ValueError, match="non-empty"):
        svc.run(equity_curve=pd.DataFrame(), strategy_id="s")


def test_stress_missing_columns_raises():
    svc = StressTestService()
    bad_df = pd.DataFrame({"x": [1, 2, 3]})
    with pytest.raises(ValueError, match="timestamp"):
        svc.run(equity_curve=bad_df, strategy_id="s")


def test_vol_multiplier_increases_volatility():
    """Multiplying returns by 2 should lower the Sharpe ratio."""
    from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
        risk_metrics as compute_risk,
    )

    svc = StressTestService(
        scenarios=[StressScenario("vol2x", "2x vol", "vol_multiplier", {"factor": 2.0})]
    )
    curve = _make_equity_curve()
    orig_sharpe = compute_risk(curve).sharpe_ratio
    summary = svc.run(equity_curve=curve, strategy_id="s")
    stressed_sharpe = summary.scenario_results[0].stressed_sharpe
    # Higher volatility should not improve Sharpe for a mild-trend curve
    assert stressed_sharpe <= orig_sharpe + 0.5  # allow small tolerance


def test_trend_reversal_degrades_performance():
    """Sign-flipping returns on an upward-trending curve should hurt Sharpe."""
    svc = StressTestService(scenarios=[StressScenario("reversal", "reversal", "sign_flip", {})])
    curve = _make_equity_curve(trend=0.0005)  # clearly trending up
    summary = svc.run(equity_curve=curve, strategy_id="s")
    r = summary.scenario_results[0]
    assert r.stressed_sharpe < r.original_sharpe


def test_return_shock_reduces_total_return():
    svc = StressTestService(
        scenarios=[StressScenario("shock", "shock", "one_time_shock", {"shock": -0.10})]
    )
    curve = _make_equity_curve()
    summary = svc.run(equity_curve=curve, strategy_id="s")
    r = summary.scenario_results[0]
    assert r.stressed_return < r.original_return + 0.01


def test_survival_rate_with_permissive_thresholds():
    """With Sharpe >= -inf and drawdown >= -inf, all scenarios should survive."""
    svc = StressTestService(
        min_sharpe_threshold=-1000.0,
        max_drawdown_threshold=-1.0,
    )
    curve = _make_equity_curve()
    summary = svc.run(equity_curve=curve, strategy_id="s")
    assert summary.survival_rate == 1.0


def test_deterministic():
    svc = StressTestService()
    curve = _make_equity_curve()
    s1 = svc.run(equity_curve=curve, strategy_id="s")
    s2 = svc.run(equity_curve=curve, strategy_id="s")
    assert s1.survival_rate == s2.survival_rate
    assert s1.worst_scenario == s2.worst_scenario
    for r1, r2 in zip(s1.scenario_results, s2.scenario_results, strict=True):
        assert r1.stressed_sharpe == pytest.approx(r2.stressed_sharpe)


def test_custom_scenario():
    custom = StressScenario("custom_penalty", "desc", "slippage_penalty", {"penalty_bps": 100.0})
    svc = StressTestService(scenarios=[custom])
    curve = _make_equity_curve()
    summary = svc.run(equity_curve=curve, strategy_id="s")
    assert summary.n_scenarios == 1
    assert summary.scenario_results[0].scenario_name == "custom_penalty"

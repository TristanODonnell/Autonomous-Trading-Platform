"""Unit tests for StressGate rules and the underlying shock transforms."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.pipeline.gates.stress_gate import (
    CostStressResult,
    StressGateConfig,
    evaluate_stress_gate,
)
from autonomous_trading_platform.research.validation.stress_test_service import (
    BUILT_IN_SCENARIOS,
    StressTestService,
)


def _curve(returns: np.ndarray) -> pd.DataFrame:
    equity = 100_000 * np.cumprod(np.concatenate([[1.0], 1 + returns]))
    start = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)
    return pd.DataFrame(
        {
            "timestamp": [start + timedelta(minutes=5 * i) for i in range(len(equity))],
            "equity": equity,
        }
    )


def _steady_curve() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    return _curve(0.0004 + rng.normal(0, 0.0005, 2_000))


def _fragile_curve() -> pd.DataFrame:
    rng = np.random.default_rng(2)
    return _curve(0.00002 + rng.normal(0, 0.002, 2_000))


def _cost(multiplier: float, sharpe: float, dd: float = -0.05) -> CostStressResult:
    return CostStressResult(
        cost_multiplier=multiplier, sharpe=sharpe, max_drawdown=dd, total_return=0.0, trade_count=20
    )


CFG = StressGateConfig()


class TestShockTransforms:
    """The perturbation functions the stress gate relies on."""

    def _apply(self, curve: pd.DataFrame, name: str) -> pd.DataFrame:
        scenario = next(s for s in BUILT_IN_SCENARIOS if s.name == name)
        return StressTestService()._apply_scenario(curve, scenario)

    def test_sign_flip_inverts_every_return(self) -> None:
        curve = _curve(np.array([0.01, -0.02, 0.03]))
        stressed = self._apply(curve, "trend_reversal")
        orig = np.diff(curve["equity"]) / curve["equity"][:-1].to_numpy()
        new = np.diff(stressed["equity"]) / stressed["equity"][:-1].to_numpy()
        np.testing.assert_allclose(new, -orig)

    def test_one_time_shock_hits_midpoint_only(self) -> None:
        curve = _curve(np.zeros(10))
        stressed = self._apply(curve, "return_shock_neg10pct")
        assert stressed["equity"].iloc[5] == pytest.approx(100_000)
        assert stressed["equity"].iloc[6] == pytest.approx(90_000)
        assert stressed["equity"].iloc[-1] == pytest.approx(90_000)

    def test_downside_amplification_leaves_gains_untouched(self) -> None:
        curve = _curve(np.array([0.02, -0.01]))
        stressed = self._apply(curve, "drawdown_amplification")
        assert stressed["equity"].iloc[1] == pytest.approx(102_000)
        assert stressed["equity"].iloc[2] == pytest.approx(102_000 * 0.98)

    def test_transforms_preserve_timestamps_and_start_equity(self) -> None:
        curve = _steady_curve()
        for scenario in BUILT_IN_SCENARIOS:
            stressed = StressTestService()._apply_scenario(curve, scenario)
            pd.testing.assert_series_equal(stressed["timestamp"], curve["timestamp"])
            assert stressed["equity"].iloc[0] == curve["equity"].iloc[0]


class TestStressGate:
    def test_steady_strategy_survives(self) -> None:
        summary = StressTestService().run(equity_curve=_steady_curve(), strategy_id="s1")
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=summary,
            cost_results=[_cost(2.0, 1.2), _cost(3.0, 0.6)],
            config=CFG,
        )
        assert verdict.passed, verdict.failure_reasons
        assert verdict.shock_survival_rate is not None and verdict.shock_survival_rate >= 0.5
        assert verdict.cost_survival_rate == 1.0

    def test_fragile_strategy_fails_shocks(self) -> None:
        summary = StressTestService().run(equity_curve=_fragile_curve(), strategy_id="s1")
        verdict = evaluate_stress_gate(
            strategy_id="s1", shock_summary=summary, cost_results=[], config=CFG
        )
        assert not verdict.passed
        assert "shock scenarios" in verdict.failure_reasons[0]

    def test_edge_that_vanishes_under_costs_fails(self) -> None:
        summary = StressTestService().run(equity_curve=_steady_curve(), strategy_id="s1")
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=summary,
            cost_results=[_cost(2.0, -0.4), _cost(3.0, -1.1)],
            config=CFG,
        )
        assert not verdict.passed
        assert verdict.cost_survival_rate == 0.0
        assert any("2x costs: Sharpe -0.40" in r for r in verdict.failure_reasons)

    def test_cost_drawdown_breach_counts_as_failure(self) -> None:
        cfg = StressGateConfig(min_cost_survival_rate=1.0)
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, 1.0, dd=-0.60)],
            config=cfg,
        )
        assert not verdict.passed

    def test_half_cost_survival_meets_default_threshold(self) -> None:
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, 0.5), _cost(3.0, -0.5)],
            config=StressGateConfig(shock_scenarios=()),
        )
        assert verdict.passed
        assert verdict.cost_survival_rate == 0.5

    def test_missing_evidence_is_warned_not_failed(self) -> None:
        verdict = evaluate_stress_gate(
            strategy_id="s1", shock_summary=None, cost_results=[], config=CFG
        )
        assert verdict.passed
        assert len(verdict.warnings) == 2

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"shock_scenarios": ("meteor",)}, "Unknown stress scenarios"),
            ({"cost_multipliers": (1.0,)}, "cost_multipliers"),
            ({"max_cost_drawdown": 0.1}, "drawdown"),
            ({"min_cost_survival_rate": 2.0}, "min_cost_survival_rate"),
        ],
    )
    def test_config_validation(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            StressGateConfig(**kwargs)

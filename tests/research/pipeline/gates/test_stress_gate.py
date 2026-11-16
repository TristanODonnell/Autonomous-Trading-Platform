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


BARS_PER_SESSION = 78  # five-minute bars in a 6.5-hour session
SESSIONS = 60
BARS_PER_YEAR = 252 * BARS_PER_SESSION


def _realistic_curve(annual_sharpe: float, seed: int = 1) -> pd.DataFrame:
    """A 60-session curve of five-minute bars whose realised Sharpe is ``annual_sharpe``.

    Bar volatility 5 bps (about 7% a year). The noise is re-centred so the
    realised mean gives the requested Sharpe exactly: over 60 sessions the
    sampling error of a Sharpe ratio is about 2, so an uncentred draw would
    make the tests depend on the seed.
    """
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.0005, SESSIONS * BARS_PER_SESSION)
    target_mean = annual_sharpe * noise.std(ddof=1) / np.sqrt(BARS_PER_YEAR)
    return _curve(noise - noise.mean() + target_mean)


def _steady_curve() -> pd.DataFrame:
    return _realistic_curve(2.0)


def _cost(
    multiplier: float, sharpe: float, dd: float = -0.05, trades: int = 20
) -> CostStressResult:
    return CostStressResult(
        cost_multiplier=multiplier,
        sharpe=sharpe,
        max_drawdown=dd,
        total_return=0.0,
        trade_count=trades,
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


def _survived(curve: pd.DataFrame) -> set[str]:
    summary = StressTestService().run(equity_curve=curve, strategy_id="s1")
    return {r.scenario_name for r in summary.scenario_results if r.survived}


class TestShocksAreReportedNotJudged:
    """Why the shock scenarios left the pass rule (plan item R11).

    On five-minute bars over 60 sessions the scenarios do not separate
    strategies: profitable curves survive the same two, losing curves the same
    one, and the only way to more is to have earned over 5% (then 10%) inside
    the window. The old threshold was 4 of 7.
    """

    @pytest.mark.parametrize("sharpe", [0.5, 1.5, 2.3])
    def test_profitable_curves_all_survive_the_same_two_scenarios(self, sharpe: float) -> None:
        assert _survived(_realistic_curve(sharpe)) == {"volatility_spike_2x", "volatility_spike_3x"}

    def test_sharpe_three_adds_only_the_five_percent_shock(self) -> None:
        """About 5% earned in the window absorbs the −5% bar; still 3 of 7, under the old bar."""
        curve = _realistic_curve(3.0)
        assert curve["equity"].iloc[-1] / curve["equity"].iloc[0] - 1 > 0.05
        assert _survived(curve) == {
            "volatility_spike_2x",
            "volatility_spike_3x",
            "return_shock_neg5pct",
        }

    @pytest.mark.parametrize("sharpe", [-0.5, -2.0])
    def test_losing_curves_survive_only_the_sign_flip(self, sharpe: float) -> None:
        assert _survived(_realistic_curve(sharpe)) == {"trend_reversal"}

    def test_shock_result_is_on_the_verdict_and_does_not_fail_it(self) -> None:
        summary = StressTestService().run(equity_curve=_steady_curve(), strategy_id="s1")
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=summary,
            cost_results=[_cost(2.0, 1.2), _cost(3.0, 0.6)],
            config=CFG,
        )
        assert verdict.passed, verdict.failure_reasons
        assert verdict.shock_summary is summary
        assert verdict.shock_survival_rate == pytest.approx(2 / 7)
        assert verdict.cost_survival_rate == 1.0

    def test_missing_shock_curve_is_a_warning_and_costs_still_decide(self) -> None:
        passing = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, 1.2), _cost(3.0, 0.6)],
            config=CFG,
        )
        failing = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, -0.4), _cost(3.0, -1.1)],
            config=CFG,
        )
        assert passing.passed and not failing.passed
        assert passing.shock_survival_rate is None
        assert "shock scenarios not reported" in passing.warnings[0]


class TestStressGate:
    def test_same_shocks_different_costs_different_verdicts(self) -> None:
        """The verdict follows the cost evidence: same curve, costs decide."""
        summary = StressTestService().run(equity_curve=_steady_curve(), strategy_id="s1")
        robust = evaluate_stress_gate(
            strategy_id="robust",
            shock_summary=summary,
            cost_results=[_cost(2.0, 1.2), _cost(3.0, 0.6)],
            config=CFG,
        )
        thin = evaluate_stress_gate(
            strategy_id="thin",
            shock_summary=summary,
            cost_results=[_cost(2.0, -0.4), _cost(3.0, -1.1)],
            config=CFG,
        )
        assert robust.passed and not thin.passed
        assert robust.shock_survival_rate == thin.shock_survival_rate

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

    def test_no_cost_results_fails(self) -> None:
        verdict = evaluate_stress_gate(
            strategy_id="s1", shock_summary=None, cost_results=[], config=CFG
        )
        assert not verdict.passed
        assert verdict.cost_survival_rate is None
        assert "no result for the 2x, 3x cost re-simulation(s)" in verdict.failure_reasons[0]

    def test_one_requested_multiplier_missing_fails_even_if_the_other_survives(self) -> None:
        verdict = evaluate_stress_gate(
            strategy_id="s1", shock_summary=None, cost_results=[_cost(2.0, 1.5)], config=CFG
        )
        assert not verdict.passed
        assert verdict.cost_survival_rate == 1.0  # of the runs that exist
        assert "no result for the 3x cost re-simulation(s)" in verdict.failure_reasons[0]

    def test_strategy_that_never_trades_fails(self) -> None:
        """A flat curve has Sharpe 0.0 and no drawdown; it must not meet `>= 0.0`."""
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, 0.0, dd=0.0, trades=0), _cost(3.0, 0.0, dd=0.0, trades=0)],
            config=CFG,
        )
        assert not verdict.passed
        assert verdict.cost_survival_rate == 0.0
        assert "2x costs: no trades" in verdict.failure_reasons[0]

    def test_zero_sharpe_with_trades_still_meets_a_zero_threshold(self) -> None:
        verdict = evaluate_stress_gate(
            strategy_id="s1",
            shock_summary=None,
            cost_results=[_cost(2.0, 0.0), _cost(3.0, 0.0)],
            config=CFG,
        )
        assert verdict.passed

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"shock_scenarios": ("meteor",)}, "Unknown stress scenarios"),
            ({"cost_multipliers": (1.0,)}, "cost_multipliers"),
            ({"cost_multipliers": ()}, "must not be empty"),
            ({"max_cost_drawdown": 0.1}, "drawdown"),
            ({"min_cost_survival_rate": 2.0}, "min_cost_survival_rate"),
        ],
    )
    def test_config_validation(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            StressGateConfig(**kwargs)

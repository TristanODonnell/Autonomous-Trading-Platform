"""
RegimeStage / StressStage / OverfittingStage with a scripted fake runner.

Covers the stage orchestration (reference-run reuse, cost multipliers reaching
the simulation request, cross-stage evidence via PipelineRunner) without the
simulation engine. Gate rules themselves are covered in tests/research/pipeline/gates/.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.return_metrics import (
    return_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.risk_metrics import (
    risk_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.stability_metrics import (
    stability_metrics,
)
from autonomous_trading_platform.research.experiments.filtering.metrics.trade_metrics import (
    TradeMetrics,
)
from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
    OverfittingGateConfig,
)
from autonomous_trading_platform.research.pipeline.gates.regime_gate import RegimeGateConfig
from autonomous_trading_platform.research.pipeline.gates.regime_labels import REGIME_COLUMNS
from autonomous_trading_platform.research.pipeline.gates.stress_gate import StressGateConfig
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
    BaseStage,
    StageDiagnostics,
    StageResult,
)
from autonomous_trading_platform.research.pipeline.stages.monte_carlo_stage import (
    MonteCarloStage,
    MonteCarloStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.overfitting_stage import (
    OverfittingStage,
    OverfittingStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.regime_stage import (
    RegimeStage,
    RegimeStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from autonomous_trading_platform.research.pipeline.stages.stress_stage import (
    StressStage,
    StressStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
    WalkForwardStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.research.validation.walk_forward_validation import (
    WalkForwardValidationService,
)
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

START = date(2024, 1, 2)
END = date(2024, 3, 29)
SYMBOLS = ["AAA", "BBB"]
BARS_PER_DAY = 4

# Script signature: (strategy_id, request, trading day) -> that day's return.
# Keyed on the calendar date so overlapping windows see the same market.
DailyReturnFn = Callable[[str, SimulationRunRequest, date], float]


def _trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _equity_curve(daily_returns: list[float], days: list[date]) -> pd.DataFrame:
    rows, equity = [], 100_000.0
    for day, r in zip(days, daily_returns, strict=True):
        per_bar = (1 + r) ** (1 / BARS_PER_DAY) - 1
        for j in range(BARS_PER_DAY):
            equity *= 1 + per_bar
            ts = datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC) + timedelta(hours=j)
            rows.append({"timestamp": ts, "equity": equity})
    return pd.DataFrame(rows)


class ScriptedRunner:
    """Builds a SimulationRunResult from a per-strategy daily-return script."""

    def __init__(self, daily_return: DailyReturnFn) -> None:
        self._daily_return = daily_return
        self.requests: list[SimulationRunRequest] = []

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.requests.append(request)
        days = _trading_days(request.start_date, request.end_date)
        rets = [self._daily_return(request.strategy_id, request, d) for d in days]
        curve = _equity_curve(rets, days)
        return SimulationRunResult(
            run_id=uuid4(),
            experiment_id=request.experiment_id,
            strategy_id=request.strategy_id,
            dataset_version=request.dataset_version,
            random_seed=request.random_seed,
            symbols=sorted(request.symbols),
            start_date=request.start_date,
            end_date=request.end_date,
            trade_count=40,
            equity_points=len(curve),
            per_bar_metric_points=0,
            status="completed",
            return_metrics=return_metrics(curve),
            risk_metrics=risk_metrics(curve),
            trade_metrics=TradeMetrics(
                total_trades=40,
                win_rate=0.6,
                avg_win=10.0,
                avg_loss=-5.0,
                profit_factor=1.5,
                largest_win=50.0,
                largest_loss=-20.0,
            ),
            stability_metrics=stability_metrics(curve),
            equity_curve=curve,
        )


class FixedLabels:
    """Label provider: odd weeks bull, even weeks bear (both dimensions covered)."""

    def __init__(self) -> None:
        self.calls = 0

    def load_daily_regimes(self, *, start_date: date, end_date: date, **_: object) -> pd.DataFrame:
        self.calls += 1
        days = _trading_days(start_date, end_date)
        frame = pd.DataFrame({"date": days, **{c: [None] * len(days) for c in REGIME_COLUMNS}})
        frame["regime_trend"] = ["bull" if (d.isocalendar().week % 2) else "bear" for d in days]
        frame["regime_volatility"] = "normal_volatility"
        return frame


def _is_bull(day: date) -> bool:
    return bool(day.isocalendar().week % 2)  # matches FixedLabels


def _cfg(sid: str) -> StrategyConfig:
    return StrategyConfig(
        strategy_id=sid,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


def _run(stage: BaseStage, survivors: list[StrategyConfig]) -> StageResult:
    return stage.run(
        survivors=survivors,
        experiment_id="exp",
        dataset_version="v1",
        random_seed=7,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )


PERMISSIVE = FilterConfig(
    min_sharpe=-1e9,
    max_drawdown=-1.0,
    min_trades=0,
    min_consistency_score=0.0,
    min_profit_factor=0.0,
    min_win_rate=0.0,
    min_total_return=-1.0,
)


# ---------------------------------------------------------------------------
# RegimeStage
# ---------------------------------------------------------------------------


def _regime_stage(
    runner: ScriptedRunner, labels: FixedLabels | None, gate: RegimeGateConfig | None = None
) -> RegimeStage:
    return RegimeStage(
        stage_config=RegimeStageConfig(
            name="regime",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            gate=gate
            or RegimeGateConfig(dimensions=("trend",), min_bars_per_regime=20, enforce=True),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
        label_provider=labels,  # type: ignore[arg-type]
    )


def _all_weather(sid: str, req: SimulationRunRequest, day: date) -> float:
    rng = np.random.default_rng(day.toordinal())
    return float(0.002 + rng.normal(0, 0.003))


def _bull_only(sid: str, req: SimulationRunRequest, day: date) -> float:
    rng = np.random.default_rng(day.toordinal())
    return float((0.004 if _is_bull(day) else -0.006) + rng.normal(0, 0.001))


class TestRegimeStage:
    def test_eliminates_bull_only_strategy(self) -> None:
        def script(sid: str, req: SimulationRunRequest, day: date) -> float:
            return _bull_only(sid, req, day) if sid == "bull_only" else _all_weather(sid, req, day)

        runner = ScriptedRunner(script)
        labels = FixedLabels()
        result = _run(_regime_stage(runner, labels), [_cfg("all_weather"), _cfg("bull_only")])

        assert [c.strategy_id for c in result.survivors] == ["all_weather"]
        assert labels.calls == 1  # labels computed once per stage, not per strategy
        failed = next(o for o in result.filter_outputs if o.strategy_id == "bull_only")
        assert any("trend:bear" in f for f in failed.filter_result.failures)
        profile = result.diagnostics["all_weather"].regime_profile
        assert profile is not None
        assert profile.by_trend.metrics_by_label["bull"].bar_count > 0

    def test_report_only_records_findings_and_eliminates_nobody(self) -> None:
        """Same two strategies as above; without enforce the bull-only one survives."""

        def script(sid: str, req: SimulationRunRequest, day: date) -> float:
            return _bull_only(sid, req, day) if sid == "bull_only" else _all_weather(sid, req, day)

        report_only = RegimeGateConfig(dimensions=("trend",), min_bars_per_regime=20)
        result = _run(
            _regime_stage(ScriptedRunner(script), FixedLabels(), report_only),
            [_cfg("all_weather"), _cfg("bull_only")],
        )

        assert [c.strategy_id for c in result.survivors] == ["all_weather", "bull_only"]
        output = next(o for o in result.filter_outputs if o.strategy_id == "bull_only")
        assert output.filter_result.passed and output.filter_result.failures == []
        verdict = result.diagnostics["bull_only"].regime_verdict
        assert verdict is not None and not verdict.enforced
        assert any("trend:bear" in f for f in verdict.findings)
        clean = result.diagnostics["all_weather"].regime_verdict
        assert clean is not None and clean.findings == ()

    def test_reuses_matching_reference_run(self) -> None:
        runner = ScriptedRunner(_all_weather)
        ref = ScriptedRunner(_all_weather).run(
            SimulationRunRequest(
                strategy_id="s1",
                strategy_config={},
                dataset_version="v1",
                random_seed=1,
                price_basis=PriceBasis.RAW,
                symbols=list(SYMBOLS),
                start_date=START,
                end_date=END,
            )
        )
        stage = _regime_stage(runner, FixedLabels())
        stage.bind_prior_results(
            [
                StageResult(
                    stage_name="mc", diagnostics={"s1": StageDiagnostics(reference_result=ref)}
                )
            ]
        )
        result = _run(stage, [_cfg("s1")])

        assert runner.requests == []  # nothing re-simulated
        assert result.simulation_results == []
        assert result.diagnostics["s1"].reference_result is ref

    def test_simulates_when_reference_window_differs(self) -> None:
        runner = ScriptedRunner(_all_weather)
        other_window = ScriptedRunner(_all_weather).run(
            SimulationRunRequest(
                strategy_id="s1",
                strategy_config={},
                dataset_version="v1",
                random_seed=1,
                price_basis=PriceBasis.RAW,
                symbols=list(SYMBOLS),
                start_date=START + timedelta(days=7),
                end_date=END,
            )
        )
        stage = _regime_stage(runner, FixedLabels())
        stage.bind_prior_results(
            [
                StageResult(
                    stage_name="mc",
                    diagnostics={"s1": StageDiagnostics(reference_result=other_window)},
                )
            ]
        )
        _run(stage, [_cfg("s1")])
        assert [r.window_role for r in runner.requests] == ["regime_reference"]

    def test_missing_labels_fall_back_to_coverage_policy(self) -> None:
        runner = ScriptedRunner(_bull_only)
        result = _run(_regime_stage(runner, None), [_cfg("s1")])
        assert [c.strategy_id for c in result.survivors] == ["s1"]  # default policy: pass
        verdict = result.diagnostics["s1"].regime_verdict
        assert verdict is not None
        assert "no regime label provider" in verdict.warnings[0]


# ---------------------------------------------------------------------------
# StressStage
# ---------------------------------------------------------------------------


def _stress_stage(runner: ScriptedRunner, **gate_kwargs) -> StressStage:
    return StressStage(
        stage_config=StressStageConfig(
            name="stress",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            gate=StressGateConfig(**gate_kwargs),
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
    )


class TestStressStage:
    def test_cost_multipliers_reach_simulation_requests(self) -> None:
        runner = ScriptedRunner(_all_weather)
        _run(_stress_stage(runner, cost_multipliers=(2.0, 3.0)), [_cfg("s1")])
        by_role = {r.window_role: r.cost_multiplier for r in runner.requests}
        assert by_role == {"stress_baseline": 1.0, "stress_cost_2x": 2.0, "stress_cost_3x": 3.0}

    def test_eliminates_strategy_whose_edge_is_eaten_by_costs(self) -> None:
        def script(sid: str, req: SimulationRunRequest, day: date) -> float:
            base = _all_weather(sid, req, day)
            if sid == "thin_edge":
                return base - 0.004 * (req.cost_multiplier - 1.0)  # costs kill the edge
            return base

        runner = ScriptedRunner(script)
        result = _run(
            _stress_stage(runner, cost_multipliers=(2.0, 3.0)), [_cfg("robust"), _cfg("thin_edge")]
        )
        assert [c.strategy_id for c in result.survivors] == ["robust"]
        verdict = result.diagnostics["thin_edge"].stress_verdict
        assert verdict is not None and verdict.cost_survival_rate == 0.0

    def test_reuses_prior_baseline_for_shocks(self) -> None:
        runner = ScriptedRunner(_all_weather)
        baseline = ScriptedRunner(_all_weather).run(
            SimulationRunRequest(
                strategy_id="s1",
                strategy_config={},
                dataset_version="v1",
                random_seed=1,
                price_basis=PriceBasis.RAW,
                symbols=list(SYMBOLS),
                start_date=START,
                end_date=END,
            )
        )
        stage = _stress_stage(runner, cost_multipliers=(2.0,))
        stage.bind_prior_results(
            [
                StageResult(
                    stage_name="regime",
                    diagnostics={"s1": StageDiagnostics(reference_result=baseline)},
                )
            ]
        )
        result = _run(stage, [_cfg("s1")])
        assert [r.window_role for r in runner.requests] == ["stress_cost_2x"]
        verdict = result.diagnostics["s1"].stress_verdict
        assert verdict is not None and verdict.shock_summary is not None

    def test_without_shock_scenarios_no_baseline_is_simulated(self) -> None:
        runner = ScriptedRunner(_all_weather)
        result = _run(
            _stress_stage(runner, cost_multipliers=(2.0,), shock_scenarios=()), [_cfg("s1")]
        )
        assert [r.window_role for r in runner.requests] == ["stress_cost_2x"]
        verdict = result.diagnostics["s1"].stress_verdict
        assert verdict is not None and verdict.passed and verdict.shock_summary is None

    def test_a_stage_with_no_cost_multipliers_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            _stress_stage(ScriptedRunner(_all_weather), cost_multipliers=())

    def test_shocks_are_reported_and_do_not_eliminate(self) -> None:
        """A profitable strategy survives under half the shock scenarios and still passes."""
        runner = ScriptedRunner(_all_weather)
        result = _run(_stress_stage(runner, cost_multipliers=(2.0, 3.0)), [_cfg("s1")])
        assert [c.strategy_id for c in result.survivors] == ["s1"]
        verdict = result.diagnostics["s1"].stress_verdict
        assert verdict is not None and verdict.shock_summary is not None
        assert verdict.shock_summary.n_scenarios == 7
        assert verdict.shock_survival_rate is not None

    def test_strategy_that_stops_trading_under_costs_is_eliminated(self) -> None:
        class NoTradesFor(ScriptedRunner):
            def run(self, request: SimulationRunRequest) -> SimulationRunResult:
                result = super().run(request)
                if request.strategy_id == "idle":
                    result.trade_count = 0
                return result

        runner = NoTradesFor(_all_weather)
        result = _run(
            _stress_stage(runner, cost_multipliers=(2.0, 3.0)), [_cfg("active"), _cfg("idle")]
        )
        assert [c.strategy_id for c in result.survivors] == ["active"]
        verdict = result.diagnostics["idle"].stress_verdict
        assert verdict is not None
        assert "no trades" in verdict.failure_reasons[0]

    def test_cost_run_that_returns_nothing_eliminates_the_strategy(self) -> None:
        class DropsThreeX(ScriptedRunner):
            def run(self, request: SimulationRunRequest) -> SimulationRunResult | None:  # type: ignore[override]
                if request.window_role == "stress_cost_3x":
                    return None
                return super().run(request)

        runner = DropsThreeX(_all_weather)
        result = _run(_stress_stage(runner, cost_multipliers=(2.0, 3.0)), [_cfg("s1")])
        assert result.survivors == []
        verdict = result.diagnostics["s1"].stress_verdict
        assert verdict is not None
        assert "no result for the 3x cost re-simulation(s)" in verdict.failure_reasons[0]


# ---------------------------------------------------------------------------
# Full pipeline: WF -> MC -> Regime -> Stress -> Overfitting
# ---------------------------------------------------------------------------


def _pipeline(runner: ScriptedRunner, *, enforce_overfitting: bool = True) -> PipelineRunner:
    """Regime/stress gates are lenient here so only the final overfitting gate eliminates."""
    return PipelineRunner(
        stages=[
            WalkForwardStage(
                stage_config=WalkForwardStageConfig(
                    name="wf",
                    train_days=30,
                    test_days=20,
                    allow_short_test_folds=True,
                    step_days=20,
                    train_filter_config=PERMISSIVE,
                    train_scoring_weights=ScoringWeights(),
                    test_filter_config=PERMISSIVE,
                    test_scoring_weights=ScoringWeights(),
                    require_all_folds=False,
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            MonteCarloStage(
                stage_config=MonteCarloStageConfig(
                    name="mc",
                    n_runs=200,
                    min_pass_rate=0.5,
                    filter_config=PERMISSIVE,
                    scoring_weights=ScoringWeights(),
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            _regime_stage(
                runner,
                FixedLabels(),
                RegimeGateConfig(
                    dimensions=("trend",),
                    min_regime_sharpe=-1e9,
                    max_regime_drawdown=-1.0,
                    min_positive_regime_fraction=0.0,
                ),
            ),
            _stress_stage(
                runner,
                cost_multipliers=(2.0,),
                min_cost_survival_rate=0.0,
            ),
            OverfittingStage(
                stage_config=OverfittingStageConfig(
                    name="overfit",
                    gate=OverfittingGateConfig(
                        max_overfitting_probability=0.6,
                        min_trade_count=10,
                        enforce=enforce_overfitting,
                    ),
                )
            ),
        ]
    )


def _curve_fit_or_steady(sid: str, req: SimulationRunRequest, day: date) -> float:
    """'curve_fit' is great in-sample and falls apart out of sample; anything else is steady."""
    if sid != "curve_fit":
        return _all_weather(sid, req, day)
    role = req.window_role or ""
    noise = float(np.random.default_rng(day.toordinal() + 17).normal(0, 0.003))
    if role.endswith("_test"):
        return -0.004 + noise  # falls apart out of sample
    if role == "mc_base":
        # Over the full window it loses money, so most resampled
        # histories have a negative Sharpe (mc_instability near 1).
        seeded = np.random.default_rng(day.toordinal() + 99)
        return float(-0.002 + seeded.normal(0, 0.01))
    return 0.004 + noise  # great in-sample


class TestPipelineEvidenceFlow:
    def test_report_only_overfitting_gate_records_the_finding_and_keeps_the_strategy(self) -> None:
        """Same two strategies as the next test; without enforce nobody is eliminated."""
        result = _pipeline(ScriptedRunner(_curve_fit_or_steady), enforce_overfitting=False).run(
            initial_configs=[_cfg("steady"), _cfg("curve_fit")],
            experiment_id="exp",
            dataset_version="v1",
            random_seed=7,
            price_basis=PriceBasis.RAW,
            initial_cash=100_000.0,
        )

        overfit = result.stage_results[-1]
        assert [c.strategy_id for c in result.final_survivors] == ["steady", "curve_fit"]
        verdict = overfit.diagnostics["curve_fit"].overfitting_verdict
        assert verdict is not None and verdict.passed and not verdict.enforced
        assert any("overfitting probability" in f for f in verdict.findings)
        output = next(o for o in overfit.filter_outputs if o.strategy_id == "curve_fit")
        assert output.filter_result.passed and output.filter_result.failures == []
        steady = overfit.diagnostics["steady"].overfitting_verdict
        assert steady is not None and steady.findings == ()

    def test_overfit_strategy_eliminated_by_final_gate(self) -> None:
        runner = ScriptedRunner(_curve_fit_or_steady)
        result = _pipeline(runner).run(
            initial_configs=[_cfg("steady"), _cfg("curve_fit")],
            experiment_id="exp",
            dataset_version="v1",
            random_seed=7,
            price_basis=PriceBasis.RAW,
            initial_cash=100_000.0,
        )

        names = [sr.stage_name for sr in result.stage_results]
        assert names == ["wf", "mc", "regime", "stress", "overfit"]
        overfit = result.stage_results[-1]
        assert [c.strategy_id for c in result.final_survivors] == ["steady"]
        assert "curve_fit" not in {c.strategy_id for c in overfit.survivors}
        analysis = overfit.diagnostics["curve_fit"].overfitting_result
        assert analysis is not None
        assert analysis.indicators.train_test_degradation is not None
        assert analysis.indicators.mc_instability is not None
        assert analysis.indicators.regime_concentration is not None

        # Regime + stress reused Monte Carlo's base run (same window).
        roles = [r.window_role for r in runner.requests]
        assert "regime_reference" not in roles
        assert "stress_baseline" not in roles

    def test_walk_forward_and_monte_carlo_record_evidence(self) -> None:
        runner = ScriptedRunner(_all_weather)
        result = _pipeline(runner).run(
            initial_configs=[_cfg("s1")],
            experiment_id="exp",
            dataset_version="v1",
            random_seed=7,
            price_basis=PriceBasis.RAW,
            initial_cash=100_000.0,
        )
        wf, mc = result.stage_results[0], result.stage_results[1]
        assert wf.diagnostics["s1"].fold_inputs
        assert mc.diagnostics["s1"].mc_aggregation is not None
        assert mc.diagnostics["s1"].reference_result is not None


# ---------------------------------------------------------------------------
# Walk-forward: a fold that fails train is still evidence (plan R2)
# ---------------------------------------------------------------------------

NEEDS_POSITIVE_SHARPE = FilterConfig(
    min_sharpe=0.5,
    max_drawdown=-1.0,
    min_trades=0,
    min_consistency_score=0.0,
    min_profit_factor=0.0,
    min_win_rate=0.0,
    min_total_return=-1.0,
)


def _slow_start(sid: str, req: SimulationRunRequest, day: date) -> float:
    """'slow_start' loses money in fold 0's train window and is steady everywhere else."""
    if sid == "slow_start" and req.window_role == "fold_0_train":
        return float(-0.004 + np.random.default_rng(day.toordinal()).normal(0, 0.003))
    return _all_weather(sid, req, day)


def _two_fold_stage(runner: ScriptedRunner, *, require_all_folds: bool) -> WalkForwardStage:
    return WalkForwardStage(
        stage_config=WalkForwardStageConfig(
            name="wf",
            train_days=30,
            test_days=20,
            allow_short_test_folds=True,
            step_days=20,
            train_filter_config=NEEDS_POSITIVE_SHARPE,
            train_scoring_weights=ScoringWeights(),
            test_filter_config=PERMISSIVE,
            test_scoring_weights=ScoringWeights(),
            require_all_folds=require_all_folds,
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
        ),
        simulation_runner=runner,  # type: ignore[arg-type]
    )


class TestWalkForwardTrainFailedFolds:
    def test_test_window_is_simulated_and_recorded_for_a_train_failed_fold(self) -> None:
        runner = ScriptedRunner(_slow_start)
        result = _run(_two_fold_stage(runner, require_all_folds=False), [_cfg("slow_start")])

        roles = [r.window_role or "" for r in runner.requests]
        assert sorted(roles) == ["fold_0_test", "fold_0_train", "fold_1_test", "fold_1_train"]
        folds = result.diagnostics["slow_start"].fold_inputs
        assert folds is not None and [f.fold_index for f in folds] == [0, 1]
        failed, passed = folds
        assert (failed.train_passed, failed.test_passed, failed.fold_passed) == (False, True, False)
        assert failed.train_sharpe < 0 < failed.test_sharpe
        assert (passed.train_passed, passed.test_passed, passed.fold_passed) == (True, True, True)
        # One of two folds, not one of one: the failed fold stays in the denominator.
        assert WalkForwardValidationService().analyze(folds).fold_consistency == 0.5
        assert [c.strategy_id for c in result.survivors] == ["slow_start"]

    def test_a_passing_test_run_does_not_rescue_a_train_failed_fold(self) -> None:
        runner = ScriptedRunner(_slow_start)
        result = _run(_two_fold_stage(runner, require_all_folds=True), [_cfg("slow_start")])
        assert result.survivors == []

    def test_other_strategies_get_the_same_verdicts_with_or_without_it(self) -> None:
        def verdicts(ids: list[str]) -> list[tuple[bool, float | None]]:
            stage = _two_fold_stage(ScriptedRunner(_slow_start), require_all_folds=False)
            result = _run(stage, [_cfg(sid) for sid in ids])
            return [
                (o.filter_result.passed, o.score.score if o.score else None)
                for o in result.filter_outputs
                if o.strategy_id == "steady"
            ]

        alone = verdicts(["steady"])
        assert len(alone) == 4  # train and test verdict for each of two folds
        assert verdicts(["steady", "slow_start"]) == alone


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestRegistry:
    def _runner(self) -> SimulationRunner:
        return cast(SimulationRunner, ScriptedRunner(_all_weather))

    def test_new_stage_types_registered(self) -> None:
        assert {"regime", "stress", "overfitting"} <= set(StageRegistry.registered_types())

    def test_load_from_yaml_dicts(self) -> None:
        runner = self._runner()
        window = {"start_date": "2024-01-02", "end_date": "2024-03-29", "symbols": ["aaa"]}
        regime = StageRegistry.load(
            {"type": "regime", "name": "r", "dimensions": ["trend"], **window}, runner
        )
        stress = StageRegistry.load(
            {"type": "stress", "name": "s", "cost_multipliers": [1.5], **window}, runner
        )
        overfit = StageRegistry.load(
            {"type": "overfitting", "name": "o", "max_overfitting_probability": 0.5}, runner
        )
        assert isinstance(regime, RegimeStage) and regime.stage_name == "r"
        assert isinstance(stress, StressStage) and stress.stage_name == "s"
        assert isinstance(overfit, OverfittingStage) and overfit.stage_name == "o"

    def test_invalid_gate_values_fail_at_load_time(self) -> None:
        runner = self._runner()
        window = {"start_date": "2024-01-02", "end_date": "2024-03-29", "symbols": ["aaa"]}
        with pytest.raises(ValueError, match="Unknown regime dimensions"):
            StageRegistry.load(
                {"type": "regime", "name": "r", "dimensions": ["x"], **window}, runner
            )
        with pytest.raises(ValueError, match="cost_multipliers"):
            StageRegistry.load(
                {"type": "stress", "name": "s", "cost_multipliers": [0.5], **window}, runner
            )
        with pytest.raises(ValueError, match="cost_multipliers must not be empty"):
            StageRegistry.load(
                {"type": "stress", "name": "s", "cost_multipliers": [], **window}, runner
            )

    def test_overfitting_stage_is_report_only_unless_yaml_says_enforce(self) -> None:
        default = StageRegistry.load({"type": "overfitting", "name": "o"}, self._runner())
        enforced = StageRegistry.load(
            {"type": "overfitting", "name": "o", "enforce": True}, self._runner()
        )
        assert isinstance(default, OverfittingStage) and default._cfg.gate.enforce is False
        assert isinstance(enforced, OverfittingStage) and enforced._cfg.gate.enforce is True

    def test_regime_stage_is_report_only_unless_yaml_says_enforce(self) -> None:
        window = {"start_date": "2024-01-02", "end_date": "2024-03-29", "symbols": ["aaa"]}
        default = StageRegistry.load({"type": "regime", "name": "r", **window}, self._runner())
        enforced = StageRegistry.load(
            {"type": "regime", "name": "r", "enforce": True, **window}, self._runner()
        )
        assert isinstance(default, RegimeStage) and default._cfg.gate.enforce is False
        assert isinstance(enforced, RegimeStage) and enforced._cfg.gate.enforce is True

    def test_removed_shock_threshold_is_rejected_not_ignored(self) -> None:
        window = {"start_date": "2024-01-02", "end_date": "2024-03-29", "symbols": ["aaa"]}
        with pytest.raises(ValueError, match="min_shock_survival_rate was removed"):
            StageRegistry.load(
                {"type": "stress", "name": "s", "min_shock_survival_rate": 0.5, **window},
                self._runner(),
            )

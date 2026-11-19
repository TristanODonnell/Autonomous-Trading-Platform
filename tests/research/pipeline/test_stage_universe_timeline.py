"""Every pipeline stage hands its universe timeline to each simulation it asks for
(research plan, package B), and a gate stage still reuses an earlier full-window run
when both ran on the same timeline."""

from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.filtering.config import ScoringWeights
from autonomous_trading_platform.research.pipeline.gates.overfitting_gate import (
    OverfittingGateConfig,
)
from autonomous_trading_platform.research.pipeline.gates.regime_gate import RegimeGateConfig
from autonomous_trading_platform.research.pipeline.gates.stress_gate import StressGateConfig
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages._gate_stage_support import (
    GateWindow,
    find_reusable_reference,
)
from autonomous_trading_platform.research.pipeline.stages.base_stage import (
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
from autonomous_trading_platform.research.pipeline.stages.simulation_stage import (
    SimulationStage,
    SimulationStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.stress_stage import (
    StressStage,
    StressStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStage,
    WalkForwardStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from tests.research.pipeline.test_robustness_stages import (
    END,
    PERMISSIVE,
    START,
    SYMBOLS,
    FixedLabels,
    ScriptedRunner,
    _all_weather,
    _cfg,
)

_TIMELINE = "universe_timeline_test"
_OTHER_TIMELINE = "universe_timeline_other"
# What the real runner reports on a timeline: the names it loaded for the run's
# dates, which are fewer than the stage config's whole list.
_LOADED_ON_TIMELINE = ["AAA"]


class TimelineRunner(ScriptedRunner):
    """Reports a run on a timeline the way SimulationRunner does."""

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        result = super().run(request)
        if request.universe_timeline_version is not None:
            result.symbols = list(_LOADED_ON_TIMELINE)
            result.universe_timeline_version = request.universe_timeline_version
        return result


def _pipeline(runner: ScriptedRunner, timeline: str | None) -> PipelineRunner:
    """Walk-forward, Monte Carlo, regime, stress, overfitting; lenient gates."""
    return PipelineRunner(
        stages=[
            WalkForwardStage(
                stage_config=WalkForwardStageConfig(
                    name="wf",
                    train_days=30,
                    test_days=20,
                    step_days=20,
                    train_filter_config=PERMISSIVE,
                    train_scoring_weights=ScoringWeights(),
                    test_filter_config=PERMISSIVE,
                    test_scoring_weights=ScoringWeights(),
                    require_all_folds=False,
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                    universe_timeline_version=timeline,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            MonteCarloStage(
                stage_config=MonteCarloStageConfig(
                    name="mc",
                    n_runs=50,
                    min_pass_rate=0.5,
                    filter_config=PERMISSIVE,
                    scoring_weights=ScoringWeights(),
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                    universe_timeline_version=timeline,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            RegimeStage(
                stage_config=RegimeStageConfig(
                    name="regime",
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                    gate=RegimeGateConfig(
                        dimensions=("trend",),
                        min_regime_sharpe=-1e9,
                        max_regime_drawdown=-1.0,
                        min_positive_regime_fraction=0.0,
                    ),
                    universe_timeline_version=timeline,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
                label_provider=FixedLabels(),  # type: ignore[arg-type]
            ),
            StressStage(
                stage_config=StressStageConfig(
                    name="stress",
                    symbols=SYMBOLS,
                    start_date=START,
                    end_date=END,
                    gate=StressGateConfig(cost_multipliers=(2.0,), min_cost_survival_rate=0.0),
                    universe_timeline_version=timeline,
                ),
                simulation_runner=runner,  # type: ignore[arg-type]
            ),
            OverfittingStage(
                stage_config=OverfittingStageConfig(
                    name="overfit",
                    gate=OverfittingGateConfig(
                        max_overfitting_probability=0.6, min_trade_count=10, enforce=False
                    ),
                )
            ),
        ]
    )


def _run_pipeline(runner: ScriptedRunner, timeline: str | None) -> None:
    result = _pipeline(runner, timeline).run(
        initial_configs=[_cfg("steady")],
        experiment_id="exp",
        dataset_version="v1",
        random_seed=7,
        price_basis=PriceBasis.RAW,
        initial_cash=100_000.0,
    )
    assert [c.strategy_id for c in result.final_survivors] == ["steady"]


def _shape(requests: list[SimulationRunRequest]) -> list[tuple]:
    return [(r.stage_name, r.window_role, r.start_date, r.end_date) for r in requests]


class TestEveryStagePassesItsTimeline:
    def test_every_request_of_a_pipeline_names_the_timeline(self) -> None:
        runner = TimelineRunner(_all_weather)

        _run_pipeline(runner, _TIMELINE)

        assert runner.requests
        assert {r.universe_timeline_version for r in runner.requests} == {_TIMELINE}
        # Walk-forward (train and test), the Monte Carlo base run and the stress runs.
        assert {"wf", "mc", "stress"} <= {r.stage_name for r in runner.requests}
        roles = {r.window_role for r in runner.requests if r.stage_name == "wf"}
        assert any("train" in str(role) for role in roles)
        assert any("test" in str(role) for role in roles)

    def test_without_a_timeline_no_request_names_one(self) -> None:
        runner = ScriptedRunner(_all_weather)

        _run_pipeline(runner, None)

        assert {r.universe_timeline_version for r in runner.requests} == {None}

    def test_simulation_stage(self) -> None:
        runner = TimelineRunner(_all_weather)
        stage = SimulationStage(
            stage_config=SimulationStageConfig(
                name="quality",
                start_date=START,
                end_date=END,
                symbols=SYMBOLS,
                filter_config=PERMISSIVE,
                scoring_weights=ScoringWeights(),
                universe_timeline_version=_TIMELINE,
            ),
            simulation_runner=runner,  # type: ignore[arg-type]
        )

        stage.run(
            survivors=[_cfg("steady")],
            experiment_id="exp",
            dataset_version="v1",
            random_seed=7,
            price_basis=PriceBasis.RAW,
            initial_cash=100_000.0,
        )

        assert [r.universe_timeline_version for r in runner.requests] == [_TIMELINE]

    def test_the_configs_default_to_no_timeline(self) -> None:
        stage_config = SimulationStageConfig(
            name="quality",
            start_date=START,
            end_date=END,
            symbols=SYMBOLS,
            filter_config=PERMISSIVE,
            scoring_weights=ScoringWeights(),
        )

        assert stage_config.universe_timeline_version is None
        assert GateWindow(SYMBOLS, START, END).universe_timeline_version is None


class TestAFullWindowRunIsStillReusedOnATimeline:
    def test_a_pipeline_on_a_timeline_asks_for_the_same_runs_as_one_without(self) -> None:
        """A run on a timeline reports the names it loaded, not the config's list. If
        the gates matched on names alone, they would stop reusing the Monte Carlo base
        run and simulate the same window again."""
        with_timeline, without = TimelineRunner(_all_weather), ScriptedRunner(_all_weather)

        _run_pipeline(with_timeline, _TIMELINE)
        _run_pipeline(without, None)

        assert _shape(with_timeline.requests) == _shape(without.requests)


def _reference(timeline: str | None, symbols: list[str]) -> SimulationRunResult:
    runner = ScriptedRunner(_all_weather)
    result = runner.run(
        SimulationRunRequest(
            strategy_id="steady",
            strategy_config={},
            dataset_version="v1",
            random_seed=7,
            price_basis=PriceBasis.RAW,
            symbols=symbols,
            start_date=START,
            end_date=END,
        )
    )
    result.universe_timeline_version = timeline
    return result


def _prior(reference: SimulationRunResult) -> list[StageResult]:
    return [
        StageResult(
            stage_name="mc",
            diagnostics={"steady": StageDiagnostics(reference_result=reference)},
        )
    ]


class TestFindReusableReference:
    def test_same_timeline_and_dates_match_whatever_names_were_loaded(self) -> None:
        reference = _reference(_TIMELINE, _LOADED_ON_TIMELINE)

        found = find_reusable_reference(
            _prior(reference), "steady", GateWindow(SYMBOLS, START, END, _TIMELINE)
        )

        assert found is reference

    def test_a_run_on_another_timeline_is_not_reused(self) -> None:
        reference = _reference(_OTHER_TIMELINE, _LOADED_ON_TIMELINE)

        assert (
            find_reusable_reference(
                _prior(reference), "steady", GateWindow(SYMBOLS, START, END, _TIMELINE)
            )
            is None
        )

    def test_a_run_on_a_timeline_is_not_reused_for_a_window_without_one(self) -> None:
        """Even when it happened to load the very names the window lists."""
        reference = _reference(_TIMELINE, SYMBOLS)

        assert (
            find_reusable_reference(_prior(reference), "steady", GateWindow(SYMBOLS, START, END))
            is None
        )

    def test_a_run_without_a_timeline_is_not_reused_for_a_window_on_one(self) -> None:
        reference = _reference(None, SYMBOLS)

        assert (
            find_reusable_reference(
                _prior(reference), "steady", GateWindow(SYMBOLS, START, END, _TIMELINE)
            )
            is None
        )

    def test_same_timeline_but_other_dates_is_not_reused(self) -> None:
        reference = _reference(_TIMELINE, _LOADED_ON_TIMELINE)
        window = GateWindow(SYMBOLS, START, date(2024, 2, 29), _TIMELINE)

        assert find_reusable_reference(_prior(reference), "steady", window) is None

    def test_without_a_timeline_the_names_still_have_to_match(self) -> None:
        reference = _reference(None, SYMBOLS)

        same = find_reusable_reference(_prior(reference), "steady", GateWindow(SYMBOLS, START, END))
        other = find_reusable_reference(
            _prior(reference), "steady", GateWindow(["AAA", "ZZZ"], START, END)
        )

        assert same is reference
        assert other is None

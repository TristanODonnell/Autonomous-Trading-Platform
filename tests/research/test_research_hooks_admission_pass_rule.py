"""The admission layout's pass rule, on the stages the research hook builds.

Decided 2026-11-18: a candidate is admitted when two of its three test folds pass AND
the three test periods together, as one nine-month record, clear the same filter.
Decided 2026-11-20: that record is one continuous run over the evaluation piece.

That run is Monte Carlo's base run. Monte Carlo on its own checks a base run's Sharpe,
total return and drawdown only through its resampled paths, and resampling scatters a
losing streak: a record with a deep drawdown has mostly shallow paths. So the hook sets
``base_run_clears_filter`` for this layout, and the record answers for the filter itself
(decided 2026-11-20, after the ``deep_drawdown`` case below was measured passing).

The stages here come from ``_build_replay_experiment_definition``; only the simulations
are scripted (a daily return per strategy and date). Every strategy gains on the
selection piece.

    strategy        fold 1       fold 2       fold 3   nine months            outcome
    steady          gains        gains        gains    clears the filter      admitted
    small_loss      small loss   gains        gains    clears the filter      admitted
    deep_drawdown   heavy loss   large gain   large    +94%, drawdown -47%    out at Monte Carlo
    net_loss        heavy loss   gains        gains    -22%                   out at Monte Carlo
    one_good_fold   small loss   small loss   gains    not reached            out at walk-forward
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    ResearchWindowDataset,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.research.pipeline.admission_window import (
    EVALUATION_MONTHS,
    SELECTION_MONTHS,
    AdmissionWindowLayout,
    months_before,
)
from autonomous_trading_platform.research.pipeline.aggregation.monte_carlo_aggregator import (
    MonteCarloAggregation,
)
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.research.pipeline.pipeline_runner import (
    PipelineRunner,
    PipelineRunResult,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)
from tests.research.pipeline.test_robustness_stages import ScriptedRunner, _cfg

SYMBOLS = ("AAA", "BBB")
SELECTION_FROM = months_before(months_before(HOLDOUT_START, EVALUATION_MONTHS), SELECTION_MONTHS)
BARS_FIRST = SELECTION_FROM - timedelta(days=45)
BARS_LAST = HOLDOUT_START + timedelta(days=45)
TICK = HOLDOUT_START + timedelta(days=30)
TICK_TS = datetime.combine(TICK, time(21, 0), tzinfo=UTC)

MONTE_CARLO = "monte_carlo_structural_robustness"
STRATEGIES = ["steady", "small_loss", "deep_drawdown", "net_loss", "one_good_fold"]
GAIN, LARGE_GAIN, SMALL_LOSS, HEAVY_LOSS = 0.003, 0.010, -0.001, -0.010
# The return per day in (fold 1, fold 2, fold 3); a strategy not named gains in all three.
IN_FOLDS = {
    "small_loss": (SMALL_LOSS, GAIN, GAIN),
    "deep_drawdown": (HEAVY_LOSS, LARGE_GAIN, LARGE_GAIN),
    "net_loss": (HEAVY_LOSS, GAIN, GAIN),
    "one_good_fold": (SMALL_LOSS, SMALL_LOSS, GAIN),
}


class _TimelineStampingRunner(ScriptedRunner):
    """As the real runner does, a result names the timeline its run traded on; the
    stages match a run they can reuse by it."""

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        result = super().run(request)
        result.universe_timeline_version = request.universe_timeline_version
        return result


def _dataset() -> ResearchWindowDataset:
    return ResearchWindowDataset(
        dataset_version_id="window_dataset",
        first_day=BARS_FIRST,
        last_day=BARS_LAST,
        symbols=SYMBOLS,
    )


def _timeline() -> UniverseTimeline:
    return UniverseTimeline(
        dataset_version_id="timeline",
        window_start=BARS_FIRST,
        content_hash="not-checked-here",
        periods=[
            UniverseTimelinePeriod(
                universe_version_id="only_list",
                effective_from=datetime.combine(BARS_FIRST, time.min, tzinfo=UTC),
                effective_to=None,
                members=[UniverseTimelineMember(symbol=s) for s in SYMBOLS],
            )
        ],
    )


def _script(layout: AdmissionWindowLayout):
    def daily_return(strategy_id: str, _request: SimulationRunRequest, day: date) -> float:
        wobble = 0.0005 if day.toordinal() % 2 else -0.0005
        per_fold = IN_FOLDS.get(strategy_id, (GAIN, GAIN, GAIN))
        for fold, value in zip(layout.test_folds, per_fold, strict=True):
            if fold.first <= day <= fold.last:
                return value + wobble
        return GAIN + wobble

    return daily_return


class _Run:
    def __init__(self) -> None:
        dataset = _dataset()
        self.layout = rh._lay_out_admission_window(tick_date=TICK, research_dataset=dataset)
        self.runner = _TimelineStampingRunner(_script(self.layout))
        self.experiment = rh._build_replay_experiment_definition(
            session=None,  # type: ignore[arg-type]  # not read: dataset and timeline are given
            timestamp=TICK_TS,
            replay_context=PlatformReplayContext.create(
                symbols=list(SYMBOLS), actor="test", timestamp=TICK_TS
            ),
            simulation_runner=self.runner,
            universe_timeline=_timeline(),
            research_dataset=dataset,
            admission_layout=self.layout,
        )
        self.stages = {
            stage.stage_name: stage for stage in self.experiment.staged_pipeline_config.stages
        }
        self.result: PipelineRunResult = PipelineRunner(
            stages=self.experiment.staged_pipeline_config.stages
        ).run(
            initial_configs=[_cfg(sid) for sid in STRATEGIES],
            experiment_id=self.experiment.experiment_id,
            dataset_version=self.experiment.dataset_version,
            random_seed=7,
            price_basis=PriceBasis.RAW,
            initial_cash=100_000.0,
        )
        self.stage = {sr.stage_name: sr for sr in self.result.stage_results}

    def survivors(self, stage_name: str) -> list[str]:
        return [c.strategy_id for c in self.stage[stage_name].survivors]

    def monte_carlo(self, strategy_id: str) -> tuple[MonteCarloAggregation, SimulationRunResult]:
        diagnostics = self.stage[MONTE_CARLO].diagnostics[strategy_id]
        assert diagnostics.mc_aggregation is not None
        assert diagnostics.reference_result is not None
        return diagnostics.mc_aggregation, diagnostics.reference_result

    def dates(self, role: str, strategy_id: str) -> list[tuple[date, date]]:
        return [
            (r.start_date, r.end_date)
            for r in self.runner.requests
            if r.window_role == role and r.strategy_id == strategy_id
        ]


@pytest.fixture(scope="module")
def run() -> _Run:
    return _Run()


def test_every_stage_ran_in_order(run: _Run) -> None:
    assert [sr.stage_name for sr in run.result.stage_results] == [
        "initial_quality_filter",
        "walk_forward_robustness",
        MONTE_CARLO,
        "regime_robustness",
        "stress_robustness",
        "overfitting_gate",
    ]
    # All five gained on the selection piece, so all five reach walk-forward.
    assert run.survivors("initial_quality_filter") == STRATEGIES


def test_walk_forward_asks_for_two_of_three_test_folds(run: _Run) -> None:
    """One good fold is out. A loss in one fold is not, however heavy: walk-forward
    counts folds, it does not weigh them."""
    assert run.survivors("walk_forward_robustness") == [
        "steady",
        "small_loss",
        "deep_drawdown",
        "net_loss",
    ]


def test_a_record_with_a_drawdown_past_the_limit_is_out_though_its_paths_all_pass(
    run: _Run,
) -> None:
    """The case the rule was tightened for. ``deep_drawdown`` passed two folds of
    three and nearly doubled its money over the nine months, after losing close to
    half of it in the first three. Every resampled path clears the filter, because
    resampling scatters the losing streak. The record itself does not."""
    limit = run.stages[MONTE_CARLO]._cfg.filter_config.max_drawdown
    aggregation, record = run.monte_carlo("deep_drawdown")

    assert record.return_metrics.total_return > 0.5
    assert record.risk_metrics.max_drawdown < limit
    assert aggregation.n_passed == aggregation.n_runs
    assert aggregation.drawdown_dist.min > limit

    assert not aggregation.passed
    assert len(aggregation.failure_reasons) == 1
    assert aggregation.failure_reasons[0].startswith("base run: ")
    assert "drawdown" in aggregation.failure_reasons[0].lower()
    assert "deep_drawdown" not in run.survivors(MONTE_CARLO)


def test_a_record_that_lost_money_is_out(run: _Run) -> None:
    aggregation, record = run.monte_carlo("net_loss")

    assert record.return_metrics.total_return < 0
    assert not aggregation.passed
    assert any(reason.startswith("base run: ") for reason in aggregation.failure_reasons)
    assert "net_loss" not in run.survivors(MONTE_CARLO)


def test_a_losing_fold_does_not_fail_a_record_that_clears_the_filter(run: _Run) -> None:
    """The rule is not "every fold": ``small_loss`` failed its first fold, and its nine
    months clear the filter."""
    aggregation, _record = run.monte_carlo("small_loss")

    assert aggregation.passed
    assert aggregation.failure_reasons == []
    assert run.survivors(MONTE_CARLO) == ["steady", "small_loss"]


def test_the_record_is_one_run_over_the_evaluation_piece(run: _Run) -> None:
    evaluation = run.layout.evaluation
    for strategy_id in run.survivors("walk_forward_robustness"):
        assert run.dates("mc_base", strategy_id) == [(evaluation.first, evaluation.last)]
        _aggregation, record = run.monte_carlo(strategy_id)
        assert (record.start_date, record.end_date) == (evaluation.first, evaluation.last)
    # It is not run for a candidate walk-forward already eliminated.
    assert run.dates("mc_base", "one_good_fold") == []


def test_the_admitted_are_the_ones_that_meet_both_halves(run: _Run) -> None:
    assert [c.strategy_id for c in run.result.final_survivors] == ["steady", "small_loss"]


def test_regime_and_stress_reuse_the_nine_month_run(run: _Run) -> None:
    """They read the same days on the same timeline, so neither simulates the
    evaluation piece again."""
    roles = {r.window_role for r in run.runner.requests}
    assert "mc_base" in roles
    assert "regime_reference" not in roles
    assert "stress_baseline" not in roles


def test_every_fold_trains_on_the_selection_piece_simulated_once(run: _Run) -> None:
    selection = run.layout.selection
    roles = sorted({r.window_role for r in run.runner.requests if r.window_role})
    train_roles = [role for role in roles if role.endswith("_train")]
    assert train_roles, roles
    for strategy_id in STRATEGIES:
        trained = [day for role in train_roles for day in run.dates(role, strategy_id)]
        assert trained == [(selection.first, selection.last)], strategy_id


def test_no_simulation_reads_a_holdout_day(run: _Run) -> None:
    assert run.runner.requests
    assert all(r.end_date < HOLDOUT_START for r in run.runner.requests)
    assert all(r.start_date >= run.layout.selection.first for r in run.runner.requests)

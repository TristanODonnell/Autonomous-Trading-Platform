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

import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
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
from autonomous_trading_platform.research.pipeline.pipeline_validation import (
    pipeline_issues,
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
NEIGHBOURS = "parameter_neighbours"
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

    def reuse_loaded_bars(self) -> AbstractContextManager[None]:
        return nullcontext()


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
        NEIGHBOURS,
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


def test_every_fold_trains_on_stage_ones_run_of_the_selection_piece(run: _Run) -> None:
    """The train window is the selection piece, and stage 1 has simulated every
    strategy that reaches walk-forward over exactly those days. That run is the train
    run of every fold; walk-forward simulates none for it (plan R2, the saving on the
    train run: 635 seconds of the baseline, a tenth of the job)."""
    selection = run.layout.selection
    stage_1 = run.stage["initial_quality_filter"]
    walk_forward = run.stage["walk_forward_robustness"]

    simulated_by_stage_1 = [
        (r.strategy_id, r.start_date, r.end_date)
        for r in run.runner.requests
        if r.stage_name == "initial_quality_filter"
    ]
    assert simulated_by_stage_1 == [(sid, selection.first, selection.last) for sid in STRATEGIES]
    assert not [r for r in run.runner.requests if (r.window_role or "").endswith("_train")]

    own_run = {r.strategy_id: r for r in stage_1.simulation_results}
    entered = run.survivors("initial_quality_filter")
    assert entered == STRATEGIES
    for strategy_id in entered:
        folds = walk_forward.diagnostics[strategy_id].fold_inputs
        assert folds is not None
        assert len(folds) == len(run.layout.test_folds)
        assert {fold.train_return for fold in folds} == {
            float(own_run[strategy_id].return_metrics.total_return)
        }
        assert {fold.train_sharpe for fold in folds} == {
            float(own_run[strategy_id].risk_metrics.sharpe_ratio)
        }
    # Stage 1's runs stay stage 1's simulations: walk-forward lists its test runs.
    assert len(walk_forward.simulation_results) == len(entered) * len(run.layout.test_folds)
    assert not any(r is own for r in walk_forward.simulation_results for own in own_run.values())


def test_no_simulation_reads_a_holdout_day(run: _Run) -> None:
    assert run.runner.requests
    assert all(r.end_date < HOLDOUT_START for r in run.runner.requests)
    assert all(r.start_date >= run.layout.selection.first for r in run.runner.requests)


# --------------------------------------------------------------------------------------
# The holdout: read by no stage, reported for the final survivors (hook layout piece 4)
# --------------------------------------------------------------------------------------


def test_the_pipeline_is_handed_the_holdout_date_and_no_stage_reaches_it(run: _Run) -> None:
    config = run.experiment.staged_pipeline_config
    assert config.holdout_start == HOLDOUT_START

    last_day = {
        name: facts.last_day_read if facts is not None else None
        for name, facts in ((name, stage.validation_facts()) for name, stage in run.stages.items())
    }
    selection, evaluation = run.layout.selection, run.layout.evaluation
    assert last_day == {
        "initial_quality_filter": selection.last,
        "walk_forward_robustness": evaluation.last,
        MONTE_CARLO: evaluation.last,
        "regime_robustness": evaluation.last,
        "stress_robustness": evaluation.last,
        NEIGHBOURS: evaluation.last,
        # Runs no simulation: it reads the evidence of the stages before it.
        "overfitting_gate": None,
    }
    assert evaluation.last < HOLDOUT_START
    assert pipeline_issues(config.stages, holdout_start=HOLDOUT_START) == []


def test_each_stage_answers_to_the_holdout_rule(run: _Run) -> None:
    """The same stages, with the date moved back onto the days they read: every stage
    that runs a simulation is named, each for its own last day."""
    stages = run.experiment.staged_pipeline_config.stages

    def named(holdout_start: date) -> list[str]:
        issues = pipeline_issues(stages, holdout_start=holdout_start)
        return [issue.split("'")[1] for issue in issues if "the holdout starts on" in issue]

    gates = [
        "walk_forward_robustness",
        MONTE_CARLO,
        "regime_robustness",
        "stress_robustness",
        NEIGHBOURS,
    ]
    assert named(run.layout.evaluation.last) == gates
    assert named(run.layout.evaluation.last + timedelta(days=1)) == []
    assert named(run.layout.selection.last) == ["initial_quality_filter", *gates]


def _holdout_report(run: _Run, survivors: list, **overrides) -> tuple[dict, ScriptedRunner]:
    runner = _TimelineStampingRunner(_script(run.layout))
    arguments = {
        "simulation_runner": runner,
        "layout": run.layout,
        "survivors": survivors,
        "experiment_def": run.experiment,
        "universe_timeline": _timeline(),
        "research_dataset": _dataset(),
        **overrides,
    }
    return rh._report_holdout(**arguments), runner


def test_the_final_survivors_are_each_run_once_over_the_holdout(run: _Run) -> None:
    holdout = run.layout.holdout
    assert holdout is not None and holdout.first == HOLDOUT_START
    assert [c.strategy_id for c in run.result.final_survivors] == ["steady", "small_loss"]

    report, runner = _holdout_report(run, run.result.final_survivors)

    assert [(r.strategy_id, r.start_date, r.end_date) for r in runner.requests] == [
        ("steady", holdout.first, holdout.last),
        ("small_loss", holdout.first, holdout.last),
    ]
    for request in runner.requests:
        assert request.window_role == "holdout"
        assert request.record_run is False
        # The pipeline's own settings and the timeline it ran on.
        assert request.experiment_id == run.experiment.experiment_id
        assert request.dataset_version == run.experiment.dataset_version
        assert request.universe_timeline_version == "timeline"
        assert sorted(request.symbols) == sorted(SYMBOLS)
    assert [c["strategy_id"] for c in report["candidates"]] == ["steady", "small_loss"]
    assert (report["first"], report["last"], report["sessions"]) == (
        holdout.first.isoformat(),
        holdout.last.isoformat(),
        holdout.sessions,
    )
    assert report["scored"] == 2 and report["not_scored"] is None
    # Both gain on days outside the test folds, by the script.
    assert all(c["total_return"] > 0 and c["trade_count"] == 40 for c in report["candidates"])


def test_only_the_holdout_runs_leave_no_row(run: _Run) -> None:
    """Every run of the six stages is recorded as before; the holdout runs are not."""
    assert run.runner.requests
    assert all(request.record_run for request in run.runner.requests)
    assert all(request.window_role != "holdout" for request in run.runner.requests)


def test_a_tick_with_no_survivor_runs_nothing_over_the_holdout(run: _Run) -> None:
    report, runner = _holdout_report(run, [])

    assert runner.requests == []
    assert report["not_scored"] == "no strategy passed every stage"
    assert report["sessions"] == run.layout.holdout.sessions  # type: ignore[union-attr]


def test_a_dataset_that_ends_with_the_evaluation_piece_has_no_holdout_to_report(
    run: _Run,
) -> None:
    layout = replace(run.layout, holdout=None)

    report, runner = _holdout_report(run, run.result.final_survivors, layout=layout)

    assert runner.requests == []
    assert report["not_scored"] == "the dataset holds no holdout session yet"
    assert (report["first"], report["last"], report["sessions"]) == (None, None, 0)


def test_a_holdout_name_the_dataset_does_not_hold_is_not_cut_out(run: _Run) -> None:
    """As for the stages: the universe of the holdout's dates is run whole or not at all."""
    short = ResearchWindowDataset(
        dataset_version_id="window_dataset",
        first_day=BARS_FIRST,
        last_day=BARS_LAST,
        symbols=("AAA",),
    )

    report, runner = _holdout_report(run, run.result.final_survivors, research_dataset=short)

    assert runner.requests == []
    assert report["not_scored"] == (
        "dataset window_dataset does not hold the universe's names for the holdout: ['BBB']"
    )


def test_a_timeline_that_stops_before_the_holdout_is_not_run_on_no_names(run: _Run) -> None:
    stops_early = UniverseTimeline(
        dataset_version_id="timeline",
        window_start=BARS_FIRST,
        content_hash="not-checked-here",
        periods=[
            UniverseTimelinePeriod(
                universe_version_id="only_list",
                effective_from=datetime.combine(BARS_FIRST, time.min, tzinfo=UTC),
                effective_to=datetime.combine(HOLDOUT_START, time.min, tzinfo=UTC),
                members=[UniverseTimelineMember(symbol=s) for s in SYMBOLS],
            )
        ],
    )

    report, runner = _holdout_report(run, run.result.final_survivors, universe_timeline=stops_early)

    assert runner.requests == []
    assert report["not_scored"] == (
        "universe timeline timeline has no list in force during the holdout"
    )


def test_a_holdout_that_cannot_be_run_does_not_fail_the_step(run: _Run) -> None:
    """Whatever goes wrong, the report says so and nothing is raised: it decides
    nothing, so it cannot fail the research step either."""

    class _Unreadable:
        dataset_version_id = "timeline"

        def symbols_on_dates(self, first: date, last: date) -> list[str]:
            raise OSError("timeline file is gone")

    report, runner = _holdout_report(
        run, run.result.final_survivors, universe_timeline=_Unreadable()
    )

    assert runner.requests == []
    assert report["not_scored"] == "failed: timeline file is gone"
    assert report["scored"] == 0


# --------------------------------------------------------------------------------------
# The gate report: what each of the stages said, written down for the summary
# --------------------------------------------------------------------------------------

STAGE_NAMES = [
    "initial_quality_filter",
    "walk_forward_robustness",
    MONTE_CARLO,
    "regime_robustness",
    "stress_robustness",
    NEIGHBOURS,
    "overfitting_gate",
]


@pytest.fixture(scope="module")
def gate_report(run: _Run) -> dict:
    return rh._report_gates(run.result.stage_results)


def _entries(gate_report: dict, strategy_id: str) -> dict[str, dict]:
    (record,) = (r for r in gate_report["strategies"] if r["strategy_id"] == strategy_id)
    return {entry["stage"]: entry for entry in record["stages"]}


def test_the_gate_report_says_how_far_each_strategy_got(gate_report: dict) -> None:
    """The table at the top of this file, read back from the report."""
    assert "not_built" not in gate_report
    assert gate_report["stages"] == STAGE_NAMES
    assert {
        record["strategy_id"]: (record["passed_every_stage"], record["eliminated_at"])
        for record in gate_report["strategies"]
    } == {
        "steady": (True, None),
        "small_loss": (True, None),
        "deep_drawdown": (False, MONTE_CARLO),
        "net_loss": (False, MONTE_CARLO),
        "one_good_fold": (False, "walk_forward_robustness"),
    }
    # A strategy is recorded at the stages it entered and no further.
    assert list(_entries(gate_report, "steady")) == STAGE_NAMES
    assert list(_entries(gate_report, "net_loss")) == STAGE_NAMES[:3]
    assert list(_entries(gate_report, "one_good_fold")) == STAGE_NAMES[:2]


def test_the_gate_report_counts_what_the_funnel_counts(run: _Run, gate_report: dict) -> None:
    """Worked out separately from the same run: the stage's own counts, and the report's
    entries for the stage."""
    for stage_result in run.result.stage_results:
        entries = [
            entry
            for record in gate_report["strategies"]
            for entry in record["stages"]
            if entry["stage"] == stage_result.stage_name
        ]
        assert len(entries) == stage_result.n_entered, stage_result.stage_name
        assert sum(entry["passed"] for entry in entries) == stage_result.n_passed


def test_the_gate_report_gives_the_folds_walk_forward_counted(gate_report: dict) -> None:
    def folds(strategy_id: str) -> tuple[bool, int, list[bool]]:
        entry = _entries(gate_report, strategy_id)["walk_forward_robustness"]
        return entry["passed"], entry["folds_passed"], [f["passed"] for f in entry["folds"]]

    assert folds("steady") == (True, 3, [True, True, True])
    assert folds("small_loss") == (True, 2, [False, True, True])
    assert folds("one_good_fold") == (False, 1, [False, False, True])


def test_the_gate_report_says_why_the_record_failed_though_its_paths_all_passed(
    run: _Run, gate_report: dict
) -> None:
    """``deep_drawdown`` again: the counts say Monte Carlo removed it, the paths say
    nothing was wrong, and the reason is the nine-month record's own drawdown."""
    entry = _entries(gate_report, "deep_drawdown")[MONTE_CARLO]
    paths, record = entry["monte_carlo"], entry["reference_run"]
    limit = run.stages[MONTE_CARLO]._cfg.filter_config.max_drawdown
    evaluation = run.layout.evaluation

    assert entry["passed"] is False and paths["passed"] is False
    assert paths["paths_passed"] == paths["paths"] > 0
    assert len(entry["failures"]) == 1 and entry["failures"][0].startswith("base run: ")
    assert (record["first"], record["last"]) == (
        evaluation.first.isoformat(),
        evaluation.last.isoformat(),
    )
    assert record["total_return"] > 0.5 and record["max_drawdown"] < limit


def test_the_gate_report_carries_the_gates_that_remove_nobody(run: _Run, gate_report: dict) -> None:
    """Regime and overfitting are report-only, and the stress gate's shocks are too:
    what they found shows in no count, so the report is the only place it is kept."""
    for strategy_id in ("steady", "small_loss"):
        entries = _entries(gate_report, strategy_id)
        regime = entries["regime_robustness"]["regime"]
        stress = entries["stress_robustness"]["stress"]
        overfitting = entries["overfitting_gate"]["overfitting"]

        assert regime["enforced"] is False and regime["passed"] is True
        # These scripted runs have no regime labels, so the gate judged nothing. It
        # passes the strategy all the same, and only the report says it judged nothing.
        assert regime["dimensions"] == [] and regime["findings"] == []
        assert regime["warnings"] == [
            "insufficient regime coverage: no regime label provider configured"
        ]
        assert overfitting["enforced"] is False and overfitting["passed"] is True
        assert 0.0 <= overfitting["overfitting_probability"] <= 1.0
        # The pass rule's runs, at the stage's own cost multipliers.
        assert [cost["cost_multiplier"] for cost in stress["costs"]] == list(
            run.stages["stress_robustness"]._cfg.gate.cost_multipliers
        )
        assert stress["cost_survival_rate"] == 1.0
        assert stress["shocks"] and stress["shock_survival_rate"] is not None


# --------------------------------------------------------------------------------------
# The neighbours of the admitted (plan R13): each re-run with one setting moved a little
# --------------------------------------------------------------------------------------


def test_the_admitted_are_each_re_run_with_one_setting_moved(run: _Run) -> None:
    """``steady`` and ``small_loss`` have the same settings here (5 and 20 bars), so
    they have the same four neighbours, and each is run once."""
    evaluation = run.layout.evaluation
    asked = [r for r in run.runner.requests if r.window_role == "neighbour"]

    assert sorted(
        (
            r.strategy_config["parameters"]["short_window"],
            r.strategy_config["parameters"]["long_window"],
        )
        for r in asked
    ) == [(4, 20), (5, 16), (5, 24), (6, 20)]
    assert all((r.start_date, r.end_date) == (evaluation.first, evaluation.last) for r in asked)
    assert all(r.stage_name == NEIGHBOURS for r in asked)
    # Each survivor's own run over those days is Monte Carlo's, not simulated again.
    assert "neighbour_base" not in {r.window_role for r in run.runner.requests}
    # Nobody that was eliminated earlier has a neighbour run.
    assert run.stage[NEIGHBOURS].n_entered == 2


def test_no_run_under_a_strategys_id_has_settings_other_than_its_own(run: _Run) -> None:
    """A run's config is stored under the run's strategy id: a neighbour run under the
    id of the strategy it came from would overwrite that strategy's settings."""
    own = [r for r in run.runner.requests if r.strategy_id in STRATEGIES]
    others = [r for r in run.runner.requests if r.strategy_id not in STRATEGIES]

    assert own and others
    for request in own:
        assert request.strategy_config["parameters"] == _cfg(request.strategy_id).parameters
        assert request.window_role != "neighbour"
    assert {r.window_role for r in others} == {"neighbour"}


def test_a_neighbour_is_never_admitted_and_is_no_strategy_of_the_report(
    run: _Run, gate_report: dict
) -> None:
    assert run.survivors(NEIGHBOURS) == ["steady", "small_loss"]
    assert set(run.stage[NEIGHBOURS].diagnostics) == {"steady", "small_loss"}
    assert [c.strategy_id for c in run.result.final_survivors] == ["steady", "small_loss"]
    assert {record["strategy_id"] for record in gate_report["strategies"]} == set(STRATEGIES)


def test_the_gate_report_carries_every_neighbours_run(gate_report: dict) -> None:
    for strategy_id in ("steady", "small_loss"):
        entries = _entries(gate_report, strategy_id)
        block = entries[NEIGHBOURS]["neighbours"]

        assert block["enforced"] is False and block["passed"] is True
        assert [(n["setting"], n["from"], n["to"]) for n in block["neighbours"]] == [
            ("short_window", 5, 4),
            ("short_window", 5, 6),
            ("long_window", 20, 16),
            ("long_window", 20, 24),
        ]
        assert block["not_built"] == []
        # Judged against the strategy's own nine-month run, the one Monte Carlo made.
        assert block["own_sharpe"] == entries[MONTE_CARLO]["reference_run"]["sharpe"]
        assert block["sharpe_needed"] == pytest.approx(block["own_sharpe"] * 0.5)
        # The script gives a strategy it does not name a gain on every day, so every
        # neighbour holds and no setting is fragile.
        assert (block["run"], block["holding"], block["findings"]) == (4, 4, [])
        assert all(n["strategy_id"] not in STRATEGIES for n in block["neighbours"])


def test_building_the_gate_report_runs_nothing_and_removes_nobody(run: _Run) -> None:
    requests = len(run.runner.requests)

    rh._report_gates(run.result.stage_results)

    assert len(run.runner.requests) == requests
    assert [c.strategy_id for c in run.result.final_survivors] == ["steady", "small_loss"]


def test_the_gate_report_of_a_real_run_can_be_written_as_json(gate_report: dict) -> None:
    assert json.loads(json.dumps(gate_report, allow_nan=False)) == gate_report


def test_a_gate_report_that_cannot_be_built_does_not_fail_the_step() -> None:
    """As for the holdout report: it decides nothing, so it cannot fail the step."""
    report = rh._report_gates([object()])  # type: ignore[list-item]

    assert report["strategies"] == []
    assert report["not_built"].startswith("failed: ")

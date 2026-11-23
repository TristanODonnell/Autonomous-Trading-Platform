"""Each research stage can say no, on real simulations (research plan R7).

Every test here runs the real SimulationRunner on synthetic bars and puts one
stage of the research hook under test **as the hook builds it**, thresholds
included. The stages before it, where it needs any, pass everything, so each
strategy reaches the stage under test and the verdict is that stage's alone.

Each test shows two things: the gate fails a strategy for a reason that is true
of its real run, and the survivors change when the bars change (or, for stress,
when the costs do).

Three strategies enter, one draw from each of three families. What happens to
them is the measured outcome of fixed bars and fixed seeds, so a failure here
after a change to the simulator, a strategy or a gate means outcomes moved:
read which, before touching the expected values.

Two stages are not quite as the hook has them, and say so where it happens:
regime and overfitting are report-only in the hook (``enforce=False``), and the
regime gate's minimum of bars per regime is sized for five-minute bars.

Run with:
    python -m pytest tests/research/test_stage_gates_on_real_runs.py -v
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from tests.research.real_run_support import (
    START,
    SYMBOLS,
    build_context,
    hook_experiment,
    seed_dataset,
    short_data_root,
)
from tests.research.test_research_stages_smoke import _permissive_stage_dicts
from tests.utilities.universe_seeding import seed_universe_version

# The hook's names for its stages.
STAGE_1 = "initial_quality_filter"
WALK_FORWARD = "walk_forward_robustness"
MONTE_CARLO = "monte_carlo_structural_robustness"
REGIME = "regime_robustness"
STRESS = "stress_robustness"
OVERFITTING = "overfitting_gate"

# The same stages with gates that pass everything, by the smoke test's names.
_PASSES_EVERYTHING = {WALK_FORWARD: "wf", MONTE_CARLO: "mc", REGIME: "regime"}

_THREE_STRATEGIES = [
    {"type": family, "method": "random", "options": {"n_samples": 1}}
    for family in ("momentum", "mean_reversion", "moving_average_crossover")
]
_ALL_THREE = ["mean_reversion", "momentum", "moving_average_crossover"]


class _RealRuns:
    """Runs one hook stage on real simulations of one shape of bars."""

    def __init__(self, session: Any, data_root: Path) -> None:
        self._session = session
        self._data_root = data_root
        self._seeded: dict[str, str] = {}
        seed_universe_version(
            session,
            symbols=SYMBOLS,
            effective_from=datetime.combine(START, datetime.min.time(), tzinfo=UTC),
        )

    def stage(
        self,
        shape: str,
        under_test: str,
        *,
        after: tuple[str, ...] = (),
        adjust: Callable[[Any], None] | None = None,
    ) -> StageResult:
        """The result of ``under_test`` on ``shape``, run after pass-everything stages."""
        if shape not in self._seeded:
            self._seeded[shape] = seed_dataset(self._data_root, self._session, shape)
        ctx = build_context(self._session, self._data_root)
        experiment = hook_experiment(self._session, ctx.simulation_runner, self._seeded[shape])

        stage = {s.stage_name: s for s in experiment.staged_pipeline_config.stages}[under_test]
        if adjust is not None:
            adjust(stage)
        passes_everything = {raw["name"]: raw for raw in _permissive_stage_dicts(experiment)}
        before = [
            StageRegistry.load(passes_everything[_PASSES_EVERYTHING[name]], ctx.simulation_runner)
            for name in after
        ]
        experiment = replace(
            experiment,
            strategy_set=_THREE_STRATEGIES,
            # smoke: the cross-stage rules are about a whole pipeline, and this
            # is one stage with pass-everything stages in front of it.
            staged_pipeline_config=StagedPipelineConfig(stages=[*before, stage], smoke=True),
        )
        result = ctx.experiment_orchestration_service.run_staged_experiment(experiment)
        stage_result: StageResult = result.stage_results[-1]
        assert stage_result.stage_name == under_test
        assert stage_result.n_entered == len(_THREE_STRATEGIES)
        return stage_result


@pytest.fixture()
def runs(db_session, monkeypatch) -> Iterator[_RealRuns]:
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    with short_data_root() as data_root:
        monkeypatch.setattr(parquet_paths, "get_data_root", lambda: data_root, raising=False)
        monkeypatch.setenv("DATA_ROOT", str(data_root))
        monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
        yield _RealRuns(db_session, data_root)


def _family(strategy_id: str) -> str:
    return strategy_id.split("__")[0]


def _kept(result: StageResult) -> list[str]:
    return sorted(_family(config.strategy_id) for config in result.survivors)


def _filter_reasons(result: StageResult, family: str) -> list[str]:
    return [
        str(failure)
        for output in result.filter_outputs
        if _family(output.strategy_id) == family
        for failure in output.filter_result.failures
    ]


def _evidence(result: StageResult, family: str) -> Any:
    (diagnostics,) = [d for sid, d in result.diagnostics.items() if _family(sid) == family]
    return diagnostics


def _enforced(stage: Any) -> None:
    stage._cfg.gate = replace(stage._cfg.gate, enforce=True)


def _regime_sized_for_daily_bars(stage: Any) -> None:
    # The hook asks for five days of five-minute bars in a regime before it
    # judges one. In daily bars that is five bars.
    stage._cfg.gate = replace(stage._cfg.gate, min_bars_per_regime=5)


def test_stage_1_drops_a_strategy_that_lost_money_and_one_that_never_traded(runs) -> None:
    falling = runs.stage("rises_then_falls", STAGE_1)
    rising = runs.stage("falls_then_rises", STAGE_1)

    assert _kept(falling) == ["momentum"]
    assert _kept(rising) == ["mean_reversion", "momentum"]

    # The reasons are true of the runs themselves.
    run_of = {_family(run.strategy_id): run for run in falling.simulation_results}
    assert run_of["mean_reversion"].risk_metrics.sharpe_ratio < 0
    assert any(r.startswith("Sharpe -") for r in _filter_reasons(falling, "mean_reversion"))
    assert run_of["moving_average_crossover"].trade_count == 0
    assert "total_trades 0 < min_trades 10" in _filter_reasons(falling, "moving_average_crossover")
    # On rising prices the same mean-reversion strategy made money and is kept.
    rising_run = {_family(run.strategy_id): run for run in rising.simulation_results}
    assert rising_run["mean_reversion"].risk_metrics.sharpe_ratio > 0


def test_walk_forward_drops_a_strategy_that_fails_its_test_folds(runs) -> None:
    falling = runs.stage("rises_then_falls", WALK_FORWARD)
    rising = runs.stage("falls_then_rises", WALK_FORWARD)

    assert _kept(falling) == []
    assert _kept(rising) == ["momentum"]

    # Momentum: both test folds pass on rising prices; on falling prices its
    # first test fold loses money and neither fold passes.
    assert [fold.fold_passed for fold in _evidence(rising, "momentum").fold_inputs] == [True, True]
    falling_folds = _evidence(falling, "momentum").fold_inputs
    assert [fold.fold_passed for fold in falling_folds] == [False, False]
    assert falling_folds[0].test_sharpe < 0
    # Mean reversion made money in one test fold of two, which is not enough.
    assert [fold.fold_passed for fold in _evidence(rising, "mean_reversion").fold_inputs] == [
        False,
        False,
    ]


def test_monte_carlo_drops_on_the_paths_and_on_the_base_run(runs) -> None:
    falling = runs.stage("rises_then_falls", MONTE_CARLO)
    rising = runs.stage("falls_then_rises", MONTE_CARLO)

    assert _kept(falling) == []
    assert _kept(rising) == ["mean_reversion", "momentum"]

    needed = 0.6  # the hook's min_pass_rate
    # Too few resampled paths clear the thresholds.
    assert _evidence(falling, "mean_reversion").mc_aggregation.pass_rate < needed
    assert any(
        "paths clear the thresholds" in r for r in _filter_reasons(falling, "mean_reversion")
    )
    # Enough paths clear them, and the base run still fails a threshold no path
    # can measure: the strategy is out on the base run alone.
    assert _evidence(falling, "momentum").mc_aggregation.pass_rate >= needed
    assert any(r.startswith("base run: ") for r in _filter_reasons(falling, "momentum"))
    # On rising prices the same two strategies clear both.
    for family in ("mean_reversion", "momentum"):
        assert _evidence(rising, family).mc_aggregation.pass_rate >= needed
        assert _filter_reasons(rising, family) == []


def test_regime_gate_reports_as_the_hook_has_it_and_drops_when_enforced(runs) -> None:
    reported = runs.stage(
        "rises_then_falls", REGIME, after=(MONTE_CARLO,), adjust=_regime_sized_for_daily_bars
    )

    def enforced_for_daily_bars(stage: Any) -> None:
        _regime_sized_for_daily_bars(stage)
        _enforced(stage)

    enforced = runs.stage(
        "falls_then_rises", REGIME, after=(MONTE_CARLO,), adjust=enforced_for_daily_bars
    )

    # Report only, the hook's setting: on falling prices the rules find
    # something against every strategy, the findings are recorded, and nobody
    # is dropped.
    assert _kept(reported) == _ALL_THREE
    for family in _ALL_THREE:
        verdict = _evidence(reported, family).regime_verdict
        assert verdict.enforced is False
        assert verdict.findings
        assert verdict.passed is True
        assert verdict.failure_reasons == ()
    assert any(
        r.startswith("trend:bear Sharpe -")
        for r in _evidence(reported, "momentum").regime_verdict.findings
    )

    # Enforced, on rising prices: the two that made money in most regimes stay,
    # the one that never traded is dropped.
    assert _kept(enforced) == ["mean_reversion", "momentum"]
    assert _evidence(enforced, "momentum").regime_verdict.passed is True
    never_traded = _evidence(enforced, "moving_average_crossover").regime_verdict
    assert never_traded.passed is False
    assert any("profitable in 0/" in r for r in never_traded.failure_reasons)


def test_stress_drops_a_strategy_that_does_not_survive_higher_costs(runs) -> None:
    falling = runs.stage("rises_then_falls", STRESS)
    rising = runs.stage("falls_then_rises", STRESS)

    assert _kept(falling) == ["momentum"]
    assert _kept(rising) == ["mean_reversion", "momentum"]

    lost = _evidence(falling, "mean_reversion").stress_verdict
    assert lost.passed is False
    assert lost.cost_survival_rate == 0.0
    (at_double_costs,) = lost.cost_results
    assert at_double_costs.cost_multiplier == 2.0
    assert at_double_costs.sharpe < 0
    assert _evidence(rising, "mean_reversion").stress_verdict.cost_survival_rate == 1.0

    # The input the gate is named for: the same strategy on the same bars, with
    # costs high enough to eat what it made.
    def punishing_costs(stage: Any) -> None:
        stage._cfg.gate = replace(stage._cfg.gate, cost_multipliers=(200.0,))

    punished = runs.stage("rises_then_falls", STRESS, adjust=punishing_costs)
    assert _evidence(falling, "momentum").stress_verdict.passed is True
    assert _evidence(punished, "momentum").stress_verdict.passed is False
    assert _kept(punished) == []


def test_overfitting_gate_drops_a_strategy_that_stopped_working_after_training(runs) -> None:
    evidence_stages = (WALK_FORWARD, MONTE_CARLO, REGIME)
    turned = runs.stage("turns_late", OVERFITTING, after=evidence_stages, adjust=_enforced)
    rising = runs.stage("falls_then_rises", OVERFITTING, after=evidence_stages, adjust=_enforced)

    assert _kept(turned) == ["momentum", "moving_average_crossover"]
    assert _kept(rising) == _ALL_THREE

    limit = 0.6  # the hook's max_overfitting_probability
    overfit = _evidence(turned, "mean_reversion").overfitting_verdict
    assert overfit.passed is False
    assert overfit.analysis.overfitting_probability > limit
    assert any(r.startswith("overfitting probability") for r in overfit.failure_reasons)
    # What made it look overfit: it did well in the train windows and badly in
    # the test windows that follow them.
    steady = _evidence(rising, "mean_reversion").overfitting_verdict
    assert steady.passed is True
    assert steady.analysis.overfitting_probability < limit
    assert (
        overfit.analysis.indicators.train_test_degradation
        > steady.analysis.indicators.train_test_degradation
    )

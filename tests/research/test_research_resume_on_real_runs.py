"""A research pipeline picked up where it stopped, on real simulations (research plan R3).

The saved run is the checkpoint: a completed simulation left a row and its frames
behind under a run id derived from its inputs, and a later attempt with reuse switched
on reads it back instead of simulating it again
(``research/simulation/completed_run_reuse.py``).

Every test here runs the real SimulationRunner, engine, database and Parquet files on
synthetic bars, through all seven kinds of stage with gates that pass everything, so
each strategy reaches each stage. What is held:

- a second attempt simulates nothing and every stage says what it said before;
- an attempt killed partway and resumed says what an uninterrupted one says, and
  simulates only what was missing;
- a completed run is not handed back when it is not the run asked for: other code,
  other settings under the same id, saved frames that are gone or were changed.

Run with:
    python -m pytest tests/research/test_research_resume_on_real_runs.py -v
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from autonomous_trading_platform.research.pipeline.gate_report import build_gate_report
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from autonomous_trading_platform.research.simulation import completed_run_reuse
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

RUNNER_LOGGER = "autonomous_trading_platform.research.simulation.simulation_runner"
THIS_CODE = "1" * 40
OTHER_CODE = "2" * 40

# One draw from each of three families. On these bars the crossover never trades, so
# a run with an empty trade log is read back too.
_THREE_STRATEGIES = [
    {"type": family, "method": "random", "options": {"n_samples": 1}}
    for family in ("momentum", "mean_reversion", "moving_average_crossover")
]
STAGES = ["sim", "wf", "mc", "regime", "stress", "neighbours", "overfit"]


class _Killed(BaseException):
    """The process dying mid-simulation. Not an Exception: nothing on the way up
    catches it, so the run it interrupts is left RUNNING, as a kill leaves it."""


@dataclass
class _Attempt:
    """One attempt at the pipeline: what it returned and how many simulations the
    engine really ran for it."""

    result: Any
    simulated: int
    reloaded: int
    runner: Any

    @property
    def said(self) -> str:
        """Everything the attempt concluded, as one comparable string: every gate's
        evidence for every strategy, every run's numbers, and who survived."""
        stages = self.result.stage_results
        runs = [
            {
                "stage": stage.stage_name,
                "run_id": str(run.run_id),
                "strategy_id": run.strategy_id,
                "symbols": sorted(run.symbols),
                "sharpe": run.risk_metrics.sharpe_ratio,
                "total_return": run.return_metrics.total_return,
                "max_drawdown": run.risk_metrics.max_drawdown,
                "win_rate": run.trade_metrics.win_rate,
                "consistency": run.stability_metrics.consistency_score,
                "trade_count": run.trade_count,
                "equity_points": run.equity_points,
                "per_bar_metric_points": run.per_bar_metric_points,
                "warmup_shortfall": run.warmup_shortfall_by_symbol,
            }
            for stage in stages
            for run in stage.simulation_results
        ]
        return json.dumps(
            {
                "gates": build_gate_report(stages),
                "runs": sorted(runs, key=lambda r: (r["stage"], r["run_id"])),
                "survivors": {s.stage_name: [c.strategy_id for c in s.survivors] for s in stages},
                "final": [c.strategy_id for c in self.result.final_survivors],
            },
            sort_keys=True,
            default=str,
        )


class _Pipeline:
    """The seven stages on one dataset, as many attempts as a test asks for, all on
    one database and one data root: what a resumed job has."""

    def __init__(self, session: Any, data_root: Path) -> None:
        self._session = session
        self._data_root = data_root
        seed_universe_version(
            session,
            symbols=SYMBOLS,
            effective_from=datetime.combine(START, datetime.min.time(), tzinfo=UTC),
        )
        self._dataset = seed_dataset(data_root, session, "rises_then_falls")

    def attempt(
        self,
        *,
        reuse: bool,
        kill_after: int | None = None,
        stages: list[str] | None = None,
        initial_cash: float | None = None,
    ) -> _Attempt:
        ctx = build_context(self._session, self._data_root)
        runner = ctx.simulation_runner
        if reuse:
            runner.reuse_completed_runs()
        simulated = _count_engine_runs(runner, kill_after=kill_after)

        experiment = hook_experiment(self._session, runner, self._dataset)
        raw_stages = _permissive_stage_dicts(experiment)
        raw_stages.insert(
            len(raw_stages) - 1,
            {
                "type": "neighbour",
                "name": "neighbours",
                "symbols": list(experiment.symbols),
                "start_date": experiment.start_date.isoformat(),
                "end_date": experiment.end_date.isoformat(),
            },
        )
        wanted = stages or STAGES
        built = [StageRegistry.load(raw, runner) for raw in raw_stages if raw["name"] in wanted]
        experiment = replace(
            experiment,
            strategy_set=_THREE_STRATEGIES,
            # smoke: the gates pass everything, so the cross-stage rules do not apply.
            staged_pipeline_config=StagedPipelineConfig(stages=built, smoke=True),
            **({"initial_cash": initial_cash} if initial_cash is not None else {}),
        )
        try:
            result = ctx.experiment_orchestration_service.run_staged_experiment(experiment)
        except _Killed:
            self._session.rollback()
            raise
        return _Attempt(
            result=result,
            simulated=simulated[0],
            reloaded=len(runner.reloaded_run_ids),
            runner=runner,
        )

    def run_rows(self) -> list[Any]:
        from sqlalchemy import select

        from autonomous_trading_platform.storage.sor.models.simulation_runs import SimulationRuns

        return list(self._session.scalars(select(SimulationRuns)))

    def saved_files(self, dataset: str) -> list[Path]:
        return sorted(self._data_root.rglob(f"{dataset}/**/*.parquet"))


def _count_engine_runs(runner: Any, *, kill_after: int | None = None) -> list[int]:
    """Count the simulations the engine really runs; die in place of the one after
    ``kill_after``."""
    calls = [0]
    real_execute = runner.execution_engine.execute

    def execute(**kwargs: Any) -> Any:
        if kill_after is not None and calls[0] >= kill_after:
            raise _Killed()
        calls[0] += 1
        return real_execute(**kwargs)

    runner.execution_engine.execute = execute
    return calls


@pytest.fixture()
def this_code(monkeypatch) -> Iterator[None]:
    """The process says which commit it runs, as an image does."""
    monkeypatch.setenv("GIT_SHA", THIS_CODE)
    completed_run_reuse.code_version.cache_clear()
    yield
    completed_run_reuse.code_version.cache_clear()


@pytest.fixture()
def pipeline(db_session, monkeypatch, this_code) -> Iterator[_Pipeline]:
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    with short_data_root() as data_root:
        monkeypatch.setattr(parquet_paths, "get_data_root", lambda: data_root, raising=False)
        monkeypatch.setenv("DATA_ROOT", str(data_root))
        monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
        yield _Pipeline(db_session, data_root)


def _refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        getattr(record, "reason", "")
        for record in caplog.records
        if record.name == RUNNER_LOGGER and record.getMessage() == "simulation.reuse_refused"
    ]


def _run_as_another_commit(monkeypatch, code: str | None) -> None:
    if code is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
        monkeypatch.setattr(completed_run_reuse, "get_git_commit", lambda: "unknown")
    else:
        monkeypatch.setenv("GIT_SHA", code)
    completed_run_reuse.code_version.cache_clear()


def test_a_second_attempt_simulates_nothing_and_says_what_the_first_said(pipeline) -> None:
    first = pipeline.attempt(reuse=False)
    assert [s.stage_name for s in first.result.stage_results] == STAGES
    assert first.simulated > 0 and first.reloaded == 0
    # Every strategy reached every stage, the one that never trades among them.
    assert len(first.result.final_survivors) == len(_THREE_STRATEGIES)
    trade_counts = {
        run.trade_count for s in first.result.stage_results for run in s.simulation_results
    }
    assert 0 in trade_counts and max(trade_counts) > 0

    second = pipeline.attempt(reuse=True)

    assert second.simulated == 0
    assert second.reloaded == first.simulated
    assert second.said == first.said


def test_reuse_is_off_unless_asked_for(pipeline) -> None:
    first = pipeline.attempt(reuse=False, stages=["sim"])
    again = pipeline.attempt(reuse=False, stages=["sim"])

    assert again.simulated == first.simulated > 0
    assert again.reloaded == 0
    assert again.said == first.said


def test_an_attempt_killed_partway_resumes_and_matches_an_uninterrupted_one(pipeline) -> None:
    done_before_the_kill = 7
    with pytest.raises(_Killed):
        pipeline.attempt(reuse=False, kill_after=done_before_the_kill)
    statuses = sorted(row.status for row in pipeline.run_rows())
    # The run the kill interrupted is left RUNNING: it is not a run to read back.
    assert statuses == ["COMPLETED"] * done_before_the_kill + ["RUNNING"]

    resumed = pipeline.attempt(reuse=True)
    # Nothing reads back from here on: every run is simulated again.
    uninterrupted = pipeline.attempt(reuse=False)

    assert resumed.reloaded == done_before_the_kill
    assert resumed.simulated == uninterrupted.simulated - done_before_the_kill
    assert resumed.said == uninterrupted.said
    assert {row.status for row in pipeline.run_rows()} == {"COMPLETED"}


def test_a_run_made_by_other_code_is_simulated_again(pipeline, monkeypatch, caplog) -> None:
    first = pipeline.attempt(reuse=False, stages=["sim"])

    _run_as_another_commit(monkeypatch, OTHER_CODE)
    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        second = pipeline.attempt(reuse=True, stages=["sim"])

    assert (second.simulated, second.reloaded) == (first.simulated, 0)
    assert second.said == first.said
    reasons = _refusals(caplog)
    assert len(reasons) == first.simulated
    assert reasons[0] == (
        f"the run was made from other inputs or other code (code then {THIS_CODE}, "
        f"now {OTHER_CODE})"
    )
    # The rows now say the new code made them, and that code reads them back.
    third = pipeline.attempt(reuse=True, stages=["sim"])
    assert (third.simulated, third.reloaded) == (0, first.simulated)


def test_nothing_is_read_back_when_the_code_version_is_not_known(
    pipeline, monkeypatch, caplog
) -> None:
    first = pipeline.attempt(reuse=False, stages=["sim"])

    _run_as_another_commit(monkeypatch, None)
    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        second = pipeline.attempt(reuse=True, stages=["sim"])

    assert (second.simulated, second.reloaded) == (first.simulated, 0)
    assert set(_refusals(caplog)) == {"the code version of this process is not known"}


def test_other_cash_under_the_same_run_id_is_simulated_again(pipeline, caplog) -> None:
    """Cash is not in a run id. The fingerprint stored with the run is what tells
    the two requests apart."""
    first = pipeline.attempt(reuse=False, stages=["sim"])
    ids_first = {str(r.run_id) for r in first.result.stage_results[0].simulation_results}

    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        second = pipeline.attempt(reuse=True, stages=["sim"], initial_cash=250_000.0)

    ids_second = {str(r.run_id) for r in second.result.stage_results[0].simulation_results}
    assert ids_second == ids_first
    assert (second.simulated, second.reloaded) == (first.simulated, 0)
    assert len(_refusals(caplog)) == first.simulated


def test_saved_frames_that_were_changed_are_not_read_back(pipeline, caplog) -> None:
    """The stored metrics are the check on the saved frames: a curve that no longer
    gives them is not the run the row describes."""
    first = pipeline.attempt(reuse=False, stages=["sim"])
    changed = pipeline.saved_files("equity_curve")[0]
    table = pq.read_table(changed)
    equity = table.column("equity").to_pylist()
    equity[-1] = equity[-1] * 1.5
    column = table.schema.get_field_index("equity")
    pq.write_table(table.set_column(column, "equity", [equity]), changed)

    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        second = pipeline.attempt(reuse=True, stages=["sim"])

    assert (second.simulated, second.reloaded) == (1, first.simulated - 1)
    (reason,) = _refusals(caplog)
    assert reason.startswith("metrics recomputed from the saved frames differ from the stored")
    assert "total_return" in reason
    # Simulated again, the run says what it said the first time.
    assert second.said == first.said


def test_saved_frames_that_are_gone_are_not_read_back(pipeline, caplog) -> None:
    first = pipeline.attempt(reuse=False, stages=["sim"])
    for path in pipeline.saved_files("equity_curve"):
        path.unlink()

    with caplog.at_level(logging.WARNING, logger=RUNNER_LOGGER):
        second = pipeline.attempt(reuse=True, stages=["sim"])

    assert (second.simulated, second.reloaded) == (first.simulated, 0)
    assert second.said == first.said
    assert all(r.startswith("the saved equity curve has 0 rows") for r in _refusals(caplog))


def test_a_run_read_back_keeps_the_columns_the_stages_read(pipeline) -> None:
    """What comes back is the saved copy: the frames' numbers are the first attempt's,
    and the columns no stage reads are the ones missing."""
    first = pipeline.attempt(reuse=False, stages=["sim"])
    second = pipeline.attempt(reuse=True, stages=["sim"])

    made = {r.strategy_id: r for r in first.result.stage_results[0].simulation_results}
    back = {r.strategy_id: r for r in second.result.stage_results[0].simulation_results}
    assert made.keys() == back.keys()
    for strategy_id, run in made.items():
        again = back[strategy_id]
        assert list(again.equity_curve.columns) == [
            "run_id",
            "strategy_id",
            "timestamp",
            "equity",
            "cash",
            "positions_value",
            "drawdown",
        ]
        assert set(run.equity_curve.columns) - set(again.equity_curve.columns) == {
            "settled_cash",
            "unsettled_cash",
            "reserved_cash",
            "buying_power",
            "pending_settlement_count",
            "dividend_cash_bar",
        }
        for column in again.equity_curve.columns:
            assert list(again.equity_curve[column]) == list(run.equity_curve[column]), column
        assert set(run.trade_logs.columns) - set(again.trade_logs.columns) == {"slippage"}
        assert len(again.trade_logs) == len(run.trade_logs)
        for column in again.trade_logs.columns:
            assert list(again.trade_logs[column]) == list(run.trade_logs[column]), column

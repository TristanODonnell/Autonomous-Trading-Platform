"""Walk-forward's train run taken from stage 1, on real simulations (research plan R2).

When an earlier stage has simulated a strategy over the very days of a train window,
walk-forward takes that run and simulates none. The claim that makes this safe is that
the two are the same run: the same settings on the same bars, whatever stage asked and
whatever seed it asked with. The scripted tests cannot show that; these do, with the
real SimulationRunner and engine on synthetic bars.

Two pipelines on the same bars, differing in one day of stage 1's window:

    taken       stage 1 ends on the last day of fold 1's train window
    simulated   stage 1 ends a day earlier, so walk-forward simulates fold 1's train run

Run with:
    python -m pytest tests/research/test_train_run_taken_on_real_runs.py -v
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
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

_THREE_STRATEGIES = [
    {"type": family, "method": "random", "options": {"n_samples": 1}}
    for family in ("momentum", "mean_reversion", "moving_average_crossover")
]


class _TwoStages:
    """Stage 1 and walk-forward on one dataset, with stage 1's last day chosen."""

    def __init__(self, session: Any, data_root: Any) -> None:
        self._session, self._data_root = session, data_root
        seed_universe_version(
            session,
            symbols=SYMBOLS,
            effective_from=datetime.combine(START, datetime.min.time(), tzinfo=UTC),
        )
        self._dataset = seed_dataset(data_root, session, "rises_then_falls")

    def run(self, *, stage_1_ends_before_train_end_by: int) -> tuple[Any, Any, list[Any]]:
        """Stage 1's result, walk-forward's, and the requests the engine really ran."""
        ctx = build_context(self._session, self._data_root)
        runner = ctx.simulation_runner
        requests: list[Any] = []
        real_run = runner.run

        def run(request: Any) -> Any:
            requests.append(request)
            return real_run(request)

        runner.run = run
        experiment = hook_experiment(self._session, runner, self._dataset)
        raw = {stage["name"]: stage for stage in _permissive_stage_dicts(experiment)}
        walk_forward: Any = StageRegistry.load(raw["wf"], runner)
        first_train_end = walk_forward._cfg.fold_windows()[0][1]
        stage_1_end = first_train_end - timedelta(days=stage_1_ends_before_train_end_by)
        stage_1 = StageRegistry.load({**raw["sim"], "end_date": stage_1_end.isoformat()}, runner)
        experiment = replace(
            experiment,
            strategy_set=_THREE_STRATEGIES,
            # smoke: the filters pass everything, and stage 1 is given its own window.
            staged_pipeline_config=StagedPipelineConfig(stages=[stage_1, walk_forward], smoke=True),
        )
        result = ctx.experiment_orchestration_service.run_staged_experiment(experiment)
        selected, walked = result.stage_results
        return selected, walked, requests


@pytest.fixture()
def two_stages(db_session, monkeypatch) -> Iterator[_TwoStages]:
    from autonomous_trading_platform.storage.parquet import paths as parquet_paths

    with short_data_root() as data_root:
        monkeypatch.setattr(parquet_paths, "get_data_root", lambda: data_root, raising=False)
        monkeypatch.setenv("DATA_ROOT", str(data_root))
        monkeypatch.setenv("PARQUET_DATA_ROOT", str(data_root))
        yield _TwoStages(db_session, data_root)


def _train_requests(requests: list[Any], fold: int) -> list[str]:
    return [r.strategy_id for r in requests if r.window_role == f"fold_{fold}_train"]


def test_a_train_run_taken_from_stage_1_is_the_run_walk_forward_would_have_made(
    two_stages: _TwoStages,
) -> None:
    selected, taken, asked_taken = two_stages.run(stage_1_ends_before_train_end_by=0)
    _, simulated, asked_simulated = two_stages.run(stage_1_ends_before_train_end_by=1)

    strategies = [run.strategy_id for run in selected.simulation_results]
    assert len(strategies) == len(_THREE_STRATEGIES)
    # Fold 1's train window is stage 1's window in the first pipeline and not in the
    # second; fold 2's train window is stage 1's in neither.
    assert _train_requests(asked_taken, 0) == []
    assert sorted(_train_requests(asked_simulated, 0)) == sorted(strategies)
    assert sorted(_train_requests(asked_taken, 1)) == sorted(strategies)
    assert len(asked_simulated) == len(asked_taken) + len(strategies)

    # The two train runs were asked for by different stages with different seeds, and
    # they are the same run: every number of every fold is equal.
    for strategy_id in strategies:
        folds_taken = taken.diagnostics[strategy_id].fold_inputs
        folds_simulated = simulated.diagnostics[strategy_id].fold_inputs
        assert len(folds_taken) == 2
        assert folds_taken == folds_simulated, strategy_id
    assert [c.strategy_id for c in taken.survivors] == [c.strategy_id for c in simulated.survivors]
    assert [(o.strategy_id, o.filter_result.passed) for o in taken.filter_outputs] == [
        (o.strategy_id, o.filter_result.passed) for o in simulated.filter_outputs
    ]
    # At least one strategy traded on the train window, so equal is not "both empty".
    own_run = {run.strategy_id: run for run in selected.simulation_results}
    assert any(own_run[sid].trade_count > 0 for sid in strategies)
    for strategy_id in strategies:
        assert taken.diagnostics[strategy_id].fold_inputs[0].train_return == float(
            own_run[strategy_id].return_metrics.total_return
        )

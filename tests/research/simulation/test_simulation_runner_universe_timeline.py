"""SimulationRunner on a universe timeline (research plan, package B).

A request names the timeline by its dataset version id. The runner reads it once, loads
bars for the names of the lists in force during the run's own dates, and hands the
timeline to the engine. A request with no timeline is run as before.

Timeline used here (a rotation takes effect at 22:00 UTC on the month's last session):

  january   2025-01-02 22:00 up to 2025-01-31 22:00   AAA, BBB
  february  2025-01-31 22:00 up to 2025-02-28 22:00   BBB, CCC
  march_on  2025-02-28 22:00 onwards                  CCC, DDD
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
)

_METRICS = [
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_return_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_risk_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_trade_metrics",
    "autonomous_trading_platform.research.simulation.simulation_runner.compute_stability_metrics",
]
_TIMELINE_ID = "universe_timeline_test"
_ALL = ["AAA", "BBB", "CCC", "DDD"]


@pytest.fixture(autouse=True)
def _patch_metrics():
    fakes = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    fakes[0].total_return = 0.0
    fakes[1].sharpe_ratio = 0.0
    fakes[1].max_drawdown = 0.0
    fakes[1].volatility = 0.0
    fakes[2].total_trades = 0
    fakes[2].win_rate = 0.0
    fakes[3].consistency_score = 0.0
    with (
        patch(_METRICS[0], return_value=fakes[0]),
        patch(_METRICS[1], return_value=fakes[1]),
        patch(_METRICS[2], return_value=fakes[2]),
        patch(_METRICS[3], return_value=fakes[3]),
    ):
        yield


def _period(version_id: str, symbols: list[str], start: datetime, end: datetime | None):
    return UniverseTimelinePeriod(
        universe_version_id=version_id,
        effective_from=start,
        effective_to=end,
        members=[UniverseTimelineMember(symbol=symbol) for symbol in symbols],
    )


def _timeline() -> UniverseTimeline:
    jan = datetime(2025, 1, 2, 22, 0, tzinfo=UTC)
    feb = datetime(2025, 1, 31, 22, 0, tzinfo=UTC)
    mar = datetime(2025, 2, 28, 22, 0, tzinfo=UTC)
    return UniverseTimeline(
        dataset_version_id=_TIMELINE_ID,
        window_start=jan.date(),
        content_hash="unused",
        periods=[
            _period("january", ["AAA", "BBB"], jan, feb),
            _period("february", ["BBB", "CCC"], feb, mar),
            _period("march_on", ["CCC", "DDD"], mar, None),
        ],
    )


class _Source:
    """Stands in for the loader: gives the timeline for its id, and counts the reads."""

    def __init__(self, timeline: UniverseTimeline) -> None:
        self.timeline = timeline
        self.calls: list[str] = []

    def __call__(self, version_id: str) -> UniverseTimeline:
        self.calls.append(version_id)
        if version_id != self.timeline.dataset_version_id:
            raise LookupError(f"no timeline {version_id}")
        return self.timeline


def _execution_result() -> MagicMock:
    result = MagicMock()
    for name in ("trade_logs", "equity_curve", "per_bar_metrics", "positions", "signal_log"):
        setattr(result, name, pd.DataFrame())
    return result


def _runner(source: _Source | None) -> tuple[SimulationRunner, MagicMock, MagicMock]:
    resolved = MagicMock()
    resolved.metadata = {}
    resolved.dataset = MagicMock()
    loader = MagicMock()

    def load_window(**kwargs: Any) -> MagicMock:
        window = MagicMock()
        window.warmup_timestamps = set()
        window.warmup_shortfall_by_symbol = {}
        window.symbols = list(kwargs["symbols"])
        return window

    loader.load_window.side_effect = load_window
    run_repo = MagicMock()
    run_repo.get_by_run_id.return_value = MagicMock(execution_config={}, metrics_snapshot_id=None)
    metrics_repo = MagicMock()
    metrics_repo.to_row.return_value = MagicMock()
    engine = MagicMock()
    engine.execute.return_value = _execution_result()
    runner = SimulationRunner(
        dataset_resolver=MagicMock(**{"resolve_bars_dataset.return_value": resolved}),
        window_loader=loader,
        result_recorder=MagicMock(),
        execution_engine=engine,
        context_builder=MagicMock(),
        simulated_execution_service=MagicMock(),
        simulation_run_repository=run_repo,
        strategy_config_repository=MagicMock(),
        experiment_repository=None,
        metrics_summary_repository=metrics_repo,
        manifest_service=None,
        strategy_factory=MagicMock(**{"build.return_value": MagicMock()}),
        feature_dependency_resolver=None,
        corporate_action_source=None,
        universe_timeline_source=source,
    )
    return runner, engine, loader


def _request(start: date, end: date, **overrides: Any) -> SimulationRunRequest:
    fields: dict[str, Any] = dict(
        strategy_id="s",
        strategy_config={"type": "stub", "parameters": {"price_change_threshold": 0.0}},
        dataset_version="raw_bars_v",
        random_seed=1,
        price_basis=PriceBasis.RAW,
        symbols=list(_ALL),
        start_date=start,
        end_date=end,
        universe_timeline_version=_TIMELINE_ID,
    )
    fields.update(overrides)
    return SimulationRunRequest(**fields)


def _loaded(loader: MagicMock) -> list[str]:
    return list(loader.load_window.call_args.kwargs["symbols"])


class TestNamesLoadedForARun:
    def test_a_run_inside_one_month_loads_that_months_list(self) -> None:
        runner, _, loader = _runner(_Source(_timeline()))

        runner.run(_request(date(2025, 1, 6), date(2025, 1, 24)))

        assert _loaded(loader) == ["AAA", "BBB"]

    def test_a_run_over_a_rotation_loads_both_lists(self) -> None:
        runner, _, loader = _runner(_Source(_timeline()))

        runner.run(_request(date(2025, 1, 20), date(2025, 2, 14)))

        assert _loaded(loader) == ["AAA", "BBB", "CCC"]

    def test_a_name_that_left_before_the_run_is_not_loaded(self) -> None:
        """It could have no bars in these dates at all, which would fail the run."""
        runner, _, loader = _runner(_Source(_timeline()))

        runner.run(_request(date(2025, 2, 3), date(2025, 2, 14)))

        assert _loaded(loader) == ["BBB", "CCC"]

    def test_two_folds_of_one_pipeline_each_load_their_own_names(self) -> None:
        runner, _, loader = _runner(_Source(_timeline()))

        runner.run(_request(date(2025, 1, 6), date(2025, 1, 24), window_role="fold_1_test"))
        first = _loaded(loader)
        runner.run(_request(date(2025, 3, 3), date(2025, 3, 21), window_role="fold_2_test"))
        second = _loaded(loader)

        assert first == ["AAA", "BBB"]
        assert second == ["CCC", "DDD"]

    def test_a_run_ending_on_a_rotation_day_also_loads_the_incoming_list(self) -> None:
        """The rotation takes effect that evening, after the last bar. The run's span
        is counted in whole days, so the incoming names are loaded and never traded:
        one name more than needed, never one fewer."""
        runner, _, loader = _runner(_Source(_timeline()))

        runner.run(_request(date(2025, 1, 20), date(2025, 1, 31)))

        assert _loaded(loader) == ["AAA", "BBB", "CCC"]


class TestTheEngineIsHandedTheTimeline:
    def test_the_timeline_read_is_the_one_the_engine_gets(self) -> None:
        source = _Source(_timeline())
        runner, engine, _ = _runner(source)

        result = runner.run(_request(date(2025, 1, 6), date(2025, 1, 24)))

        assert engine.execute.call_args.kwargs["universe_timeline"] is source.timeline
        assert result.universe_timeline_version == _TIMELINE_ID
        assert result.symbols == ["AAA", "BBB"]

    def test_a_timeline_is_read_once_for_many_runs(self) -> None:
        source = _Source(_timeline())
        runner, _, _ = _runner(source)

        for seed in (1, 2, 3):
            runner.run(_request(date(2025, 1, 6), date(2025, 1, 24), random_seed=seed))

        assert source.calls == [_TIMELINE_ID]


class TestARequestWithNoTimelineRunsAsBefore:
    def test_the_requests_own_symbols_are_loaded_and_no_timeline_is_read(self) -> None:
        source = _Source(_timeline())
        runner, engine, loader = _runner(source)

        result = runner.run(
            _request(date(2025, 1, 6), date(2025, 1, 24), universe_timeline_version=None)
        )

        assert _loaded(loader) == _ALL
        assert engine.execute.call_args.kwargs["universe_timeline"] is None
        assert result.universe_timeline_version is None
        assert source.calls == []


class TestRefused:
    def test_a_timeline_named_with_no_source_to_read_it(self) -> None:
        runner, engine, _ = _runner(None)

        with pytest.raises(ValueError, match="without a universe timeline source"):
            runner.run(_request(date(2025, 1, 6), date(2025, 1, 24)))
        engine.execute.assert_not_called()

    def test_a_request_whose_symbols_do_not_cover_the_lists_in_force(self) -> None:
        runner, engine, loader = _runner(_Source(_timeline()))

        with pytest.raises(ValueError, match=r"name symbols the request does not: \['BBB'\]"):
            runner.run(_request(date(2025, 1, 6), date(2025, 1, 24), symbols=["AAA", "CCC"]))
        loader.load_window.assert_not_called()
        engine.execute.assert_not_called()

    def test_dates_before_the_timelines_first_list(self) -> None:
        runner, _, loader = _runner(_Source(_timeline()))

        with pytest.raises(ValueError, match="no list in force between"):
            runner.run(_request(date(2024, 12, 2), date(2024, 12, 20)))
        loader.load_window.assert_not_called()

    def test_shuffled_bar_timestamps(self) -> None:
        runner, _, loader = _runner(_Source(_timeline()))

        with pytest.raises(ValueError, match="cannot shuffle bar timestamps"):
            runner.run(_request(date(2025, 1, 6), date(2025, 1, 24), shuffle_timestamp=True))
        loader.load_window.assert_not_called()

    def test_a_timeline_the_source_does_not_have(self) -> None:
        runner, _, _ = _runner(_Source(_timeline()))

        with pytest.raises(LookupError, match="no timeline other_timeline"):
            runner.run(
                _request(
                    date(2025, 1, 6), date(2025, 1, 24), universe_timeline_version="other_timeline"
                )
            )

    def test_a_refused_run_is_recorded_as_failed(self) -> None:
        runner, _, _ = _runner(None)

        with (
            patch.object(runner, "_record_run_failed") as failed,
            pytest.raises(ValueError),
        ):
            runner.run(_request(date(2025, 1, 6), date(2025, 1, 24)))

        failed.assert_called_once()


# ---------------------------------------------------------------------------
# What a run is recorded and identified under (piece 3)
# ---------------------------------------------------------------------------

_JAN = (date(2025, 1, 6), date(2025, 1, 24))


def _run(request: SimulationRunRequest, *, exits: int | None = None, manifest: bool = False):
    """Run one request; returns (result, runner). ``exits`` = rows in the engine exit log."""
    runner, engine, _ = _runner(_Source(_timeline()))
    if exits is not None:
        engine.execute.return_value.universe_exit_log = pd.DataFrame({"symbol": ["AAA"] * exits})
    if manifest:
        runner.simulated_execution_service.cost_model_summary = {}
        runner.simulated_execution_service.slippage_model_summary = {}
        runner.manifest_service = MagicMock()
    return runner.run(request), runner


def _fixed(**overrides: Any) -> SimulationRunRequest:
    return _request(*_JAN, universe_timeline_version=None, **overrides)


def _run_row(runner: SimulationRunner) -> Any:
    repository: Any = runner.simulation_run_repository  # a MagicMock in these tests
    return repository.to_row.call_args.args[0]


def _result_summary(runner: SimulationRunner) -> dict[str, Any]:
    repository: Any = runner.simulation_run_repository  # a MagicMock in these tests
    summary: dict[str, Any] = repository.get_by_run_id.return_value.execution_config[
        "result_summary"
    ]
    return summary


class TestARunOnATimelineIsRecordedUnderIt:
    def test_the_run_row_names_the_timeline_as_its_universe(self) -> None:
        _, runner = _run(_request(*_JAN))

        row = _run_row(runner)
        assert row.universe_version == _TIMELINE_ID
        assert row.execution_config["universe_timeline_version"] == _TIMELINE_ID

    def test_the_stored_results_are_filed_under_the_timeline(self) -> None:
        _, runner = _run(_request(*_JAN))

        identity = runner.result_recorder.record_results.call_args.kwargs["identity"]
        assert identity.universe_version == _TIMELINE_ID

    def test_the_manifest_names_the_timeline(self) -> None:
        _, runner = _run(_request(*_JAN), manifest=True)

        started = runner.manifest_service.upsert.call_args_list[0].args[0]
        assert started.universe_version == _TIMELINE_ID

    def test_a_run_on_a_fixed_list_is_recorded_as_before(self) -> None:
        _, runner = _run(_fixed(), manifest=True)

        row = _run_row(runner)
        assert row.universe_version == "v1"
        assert "universe_timeline_version" not in row.execution_config
        identity = runner.result_recorder.record_results.call_args.kwargs["identity"]
        assert identity.universe_version == "v1"
        assert runner.manifest_service.upsert.call_args_list[0].args[0].universe_version == "v1"


class TestTheRunId:
    def test_the_same_names_and_dates_on_a_timeline_get_another_run_id(self) -> None:
        on_timeline, _ = _run(_request(*_JAN))
        fixed, _ = _run(_fixed())

        assert on_timeline.run_id != fixed.run_id

    def test_two_timelines_get_two_run_ids(self) -> None:
        runner, _, _ = _runner(None)

        first = runner._derive_run_id(_request(*_JAN, universe_timeline_version="timeline_a"))
        second = runner._derive_run_id(_request(*_JAN, universe_timeline_version="timeline_b"))

        assert first != second

    def test_the_same_run_on_the_same_timeline_gets_the_same_run_id(self) -> None:
        first, _ = _run(_request(*_JAN))
        second, _ = _run(_request(*_JAN))

        assert first.run_id == second.run_id

    def test_a_run_on_a_fixed_list_keeps_the_run_id_it_had(self) -> None:
        """The id is built here the way it was before a request could name a timeline."""
        from uuid import uuid5

        from autonomous_trading_platform.research.simulation.simulation_runner import _RUN_NS

        request = _fixed(experiment_id="exp", stage_name="cheap", window_role="train")
        before = uuid5(
            _RUN_NS,
            ":".join(
                [
                    request.strategy_id,
                    str(request.random_seed),
                    request.dataset_version,
                    request.price_basis.value,
                    ":".join(sorted(request.symbols)),
                    str(request.start_date),
                    str(request.end_date),
                    "exp",
                    "cheap",
                    "train",
                ]
            ),
        )
        runner, _, _ = _runner(None)

        assert runner._derive_run_id(request) == before


class TestForcedExitsAreCounted:
    def test_the_count_is_on_the_result_and_beside_the_trade_count(self) -> None:
        result, runner = _run(_request(*_JAN), exits=3, manifest=True)

        assert result.universe_exit_count == 3
        assert _result_summary(runner)["universe_exit_count"] == 3
        completed = runner.manifest_service.get_by_run_id.return_value
        manifest = runner.manifest_service.to_contract(completed)
        assert manifest.artifact_manifest["summary"]["universe_exit_count"] == 3

    def test_a_timeline_run_with_no_forced_exit_records_zero_not_nothing(self) -> None:
        result, runner = _run(_request(*_JAN), exits=0)

        assert result.universe_exit_count == 0
        assert _result_summary(runner)["universe_exit_count"] == 0

    def test_a_run_on_a_fixed_list_has_no_count(self) -> None:
        result, runner = _run(_fixed(), exits=0)

        assert result.universe_exit_count is None
        assert "universe_exit_count" not in _result_summary(runner)

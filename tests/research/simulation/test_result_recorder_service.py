from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from autonomous_trading_platform.research.simulation.artifact_identity import (
    SimulationArtifactIdentity,
)
from autonomous_trading_platform.research.simulation.services.result_recorder_service import (
    ResultRecorderService,
)
from autonomous_trading_platform.storage.parquet.repositories.parquet_simulation_repository import (
    ParquetSimulationRepository,
)


@pytest.fixture()
def identity() -> SimulationArtifactIdentity:
    return SimulationArtifactIdentity(
        run_id="run-001",
        experiment_id="exp-001",
        strategy_id="strat-001",
        dataset_version="v1",
        stage_name="cheap",
        window_role="train",
        seed=42,
        start_date=date(2023, 1, 1),
        end_date=date(2023, 6, 30),
    )


@pytest.fixture()
def mock_repo() -> MagicMock:
    return MagicMock(spec=ParquetSimulationRepository)


@pytest.fixture()
def recorder(mock_repo: MagicMock) -> ResultRecorderService:
    return ResultRecorderService(parquet_simulation_repository=mock_repo)


def test_record_results_delegates_all_frames_to_parquet_repo(
    recorder: ResultRecorderService,
    mock_repo: MagicMock,
    identity: SimulationArtifactIdentity,
) -> None:
    empty = pd.DataFrame()

    recorder.record_results(
        identity=identity,
        trade_logs=empty,
        equity_curve=empty,
        per_bar_metrics=empty,
        positions=empty,
        signal_log=empty,
    )

    mock_repo.write_trade_logs.assert_called_once_with(frame=empty, identity=identity)
    mock_repo.write_equity_curve.assert_called_once_with(frame=empty, identity=identity)
    mock_repo.write_per_bar_metrics.assert_called_once_with(frame=empty, identity=identity)
    mock_repo.write_positions.assert_called_once_with(frame=empty, identity=identity)
    mock_repo.write_signal_log.assert_called_once_with(frame=empty, identity=identity)


def test_record_results_passes_same_identity_to_all_writes(
    recorder: ResultRecorderService,
    mock_repo: MagicMock,
    identity: SimulationArtifactIdentity,
) -> None:
    empty = pd.DataFrame()

    recorder.record_results(
        identity=identity,
        trade_logs=empty,
        equity_curve=empty,
        per_bar_metrics=empty,
        positions=empty,
        signal_log=empty,
    )

    for method in [
        mock_repo.write_trade_logs,
        mock_repo.write_equity_curve,
        mock_repo.write_per_bar_metrics,
        mock_repo.write_positions,
        mock_repo.write_signal_log,
    ]:
        _, kwargs = method.call_args
        assert kwargs["identity"] is identity


def test_record_results_distinct_identities_for_different_runs(
    recorder: ResultRecorderService,
    mock_repo: MagicMock,
) -> None:
    identity_a = SimulationArtifactIdentity(
        run_id="run-aaa",
        experiment_id="exp-1",
        strategy_id="strat-1",
        dataset_version="v1",
        stage_name="adhoc",
        window_role="default",
    )
    identity_b = SimulationArtifactIdentity(
        run_id="run-bbb",
        experiment_id="exp-1",
        strategy_id="strat-1",
        dataset_version="v1",
        stage_name="adhoc",
        window_role="default",
    )

    empty = pd.DataFrame()

    recorder.record_results(
        identity=identity_a,
        trade_logs=empty,
        equity_curve=empty,
        per_bar_metrics=empty,
        positions=empty,
        signal_log=empty,
    )
    recorder.record_results(
        identity=identity_b,
        trade_logs=empty,
        equity_curve=empty,
        per_bar_metrics=empty,
        positions=empty,
        signal_log=empty,
    )

    calls = mock_repo.write_equity_curve.call_args_list
    assert len(calls) == 2
    identities_used = [c.kwargs["identity"] for c in calls]
    assert identities_used[0].run_id != identities_used[1].run_id

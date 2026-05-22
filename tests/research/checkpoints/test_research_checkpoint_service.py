from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.cache.simulation_result_cache import SimulationResultCache
from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpointStatus,
    ResearchTaskType,
)
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
    ResumeMode,
    simulation_request_cache_key,
    simulation_request_checkpoint_identity,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)


class CountingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def run(self, request: SimulationRunRequest) -> SimulationRunResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated failure")
        result = MagicMock(spec=SimulationRunResult)
        result.run_id = uuid4()
        result.strategy_id = request.strategy_id
        return result


def _request(strategy_id: str = "strat-1") -> SimulationRunRequest:
    return SimulationRunRequest(
        experiment_id="exp-1",
        strategy_id=strategy_id,
        strategy_config={
            "type": "moving_average_crossover",
            "parameters": {"short_window": 5, "long_window": 20},
        },
        dataset_version="bars-v1",
        random_seed=42,
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        initial_cash=100_000.0,
        window_role="default",
        stage_name="cheap",
    )


def test_checkpoint_identity_is_deterministic() -> None:
    first = simulation_request_checkpoint_identity(_request())
    second = simulation_request_checkpoint_identity(_request())

    assert first.checkpoint_id == second.checkpoint_id
    assert first.to_dict() == second.to_dict()


def test_completed_simulation_is_skipped(tmp_path: Path) -> None:
    service = ResearchCheckpointService(persist_path=tmp_path / "checkpoints.json")
    request = _request()
    identity = simulation_request_checkpoint_identity(request)
    service.mark_completed(identity)
    runner = CountingRunner()

    execution = service.run_simulation_unit(request=request, runner=runner)

    assert execution.skipped is True
    assert execution.status == ResearchCheckpointStatus.COMPLETED
    assert runner.calls == 0


def test_missing_simulation_is_run_and_completed(tmp_path: Path) -> None:
    service = ResearchCheckpointService(persist_path=tmp_path / "checkpoints.json")
    runner = CountingRunner()

    execution = service.run_simulation_unit(request=_request(), runner=runner)

    assert execution.skipped is False
    assert execution.status == ResearchCheckpointStatus.COMPLETED
    assert runner.calls == 1


def test_failed_unit_reruns_in_failed_only_mode(tmp_path: Path) -> None:
    service = ResearchCheckpointService(persist_path=tmp_path / "checkpoints.json")
    request = _request()
    identity = simulation_request_checkpoint_identity(request)
    service.mark_failed(identity, error_message="previous failure")
    runner = CountingRunner()

    execution = service.run_simulation_unit(
        request=request,
        runner=runner,
        task_type=ResearchTaskType.WALK_FORWARD_FOLD,
        resume_mode=ResumeMode.FAILED_ONLY,
    )

    assert execution.status == ResearchCheckpointStatus.COMPLETED
    assert runner.calls == 1


def test_cache_hit_is_preferred_over_rerun(tmp_path: Path) -> None:
    cache = SimulationResultCache()
    request = _request()
    cache.record(simulation_request_cache_key(request), run_id="cached-run")
    service = ResearchCheckpointService(
        persist_path=tmp_path / "checkpoints.json",
        simulation_cache=cache,
    )
    runner = CountingRunner()

    execution = service.run_simulation_unit(request=request, runner=runner)

    assert execution.skipped is True
    assert execution.status == ResearchCheckpointStatus.CACHE_HIT
    assert runner.calls == 0


def test_failure_is_marked_with_error_message(tmp_path: Path) -> None:
    service = ResearchCheckpointService(persist_path=tmp_path / "checkpoints.json")
    request = _request()

    with pytest.raises(RuntimeError, match="simulated failure"):
        service.run_simulation_unit(request=request, runner=CountingRunner(fail=True))

    checkpoint = service.get(simulation_request_checkpoint_identity(request))
    assert checkpoint is not None
    assert checkpoint.status == ResearchCheckpointStatus.FAILED
    assert checkpoint.error_message == "simulated failure"

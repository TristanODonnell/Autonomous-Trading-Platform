from __future__ import annotations

import pytest

from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.runtime.services.runtime_job_runner import RuntimeJobRunner


class FakeRuntimeJobRunRepository:
    def __init__(self) -> None:
        self.saved: list[RuntimeJobRun] = []

    def save(self, contract: RuntimeJobRun) -> RuntimeJobRun:
        self.saved.append(contract)
        return contract


def test_runtime_job_runner_tracks_success() -> None:
    repository = FakeRuntimeJobRunRepository()
    runner = RuntimeJobRunner(repository=repository)

    result = runner.run(
        job_name="test_job",
        trigger_type="test",
        correlation_id="corr-123",
        input_summary_json={"input": "value"},
        job=lambda: "success-result",
    )

    assert result == "success-result"

    assert len(repository.saved) == 2

    running = repository.saved[0]
    completed = repository.saved[1]

    assert running.job_name == "test_job"
    assert running.status == "running"
    assert running.trigger_type == "test"
    assert running.correlation_id == "corr-123"
    assert running.input_summary_json == {"input": "value"}
    assert running.completed_at is None
    assert running.duration_ms is None
    assert running.error_message is None

    assert completed.job_run_id == running.job_run_id
    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.duration_ms is not None
    assert completed.duration_ms >= 0
    assert completed.error_message is None


def test_runtime_job_runner_tracks_failure_and_reraises_exception() -> None:
    repository = FakeRuntimeJobRunRepository()
    runner = RuntimeJobRunner(repository=repository)

    def failing_job() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        runner.run(
            job_name="failing_job",
            trigger_type="test",
            correlation_id="corr-456",
            input_summary_json={"will_fail": True},
            job=failing_job,
        )

    assert len(repository.saved) == 2

    running = repository.saved[0]
    failed = repository.saved[1]

    assert running.status == "running"

    assert failed.job_run_id == running.job_run_id
    assert failed.job_name == "failing_job"
    assert failed.status == "failed"
    assert failed.trigger_type == "test"
    assert failed.correlation_id == "corr-456"
    assert failed.input_summary_json == {"will_fail": True}
    assert failed.completed_at is not None
    assert failed.duration_ms is not None
    assert failed.duration_ms >= 0
    assert failed.error_message == "boom"


def test_runtime_job_runner_tracks_skipped_job_without_executing_job() -> None:
    repository = FakeRuntimeJobRunRepository()
    runner = RuntimeJobRunner(repository=repository)

    was_called = False

    def job() -> str:
        nonlocal was_called
        was_called = True
        return "should-not-run"

    result = runner.run(
        job_name="skipped_job",
        trigger_type="test",
        correlation_id="corr-789",
        input_summary_json={"skip_check": True},
        skip_reason="no eligible symbols",
        job=job,
    )

    assert result is None
    assert was_called is False

    assert len(repository.saved) == 1

    skipped = repository.saved[0]

    assert skipped.job_name == "skipped_job"
    assert skipped.status == "skipped"
    assert skipped.trigger_type == "test"
    assert skipped.correlation_id == "corr-789"
    assert skipped.input_summary_json == {"skip_check": True}
    assert skipped.output_summary_json == {"skip_reason": "no eligible symbols"}
    assert skipped.completed_at is not None
    assert skipped.duration_ms is not None
    assert skipped.duration_ms >= 0
    assert skipped.error_message is None


def test_runtime_job_runner_tracks_parent_child_relationship() -> None:
    repository = FakeRuntimeJobRunRepository()
    runner = RuntimeJobRunner(repository=repository)

    parent_result = runner.run(
        job_name="parent_job",
        trigger_type="test",
        correlation_id="corr-parent-child",
        job=lambda: "parent-result",
    )

    parent_final = repository.saved[-1]

    child_result = runner.run(
        job_name="child_job",
        trigger_type="test",
        parent_job_run_id=parent_final.job_run_id,
        correlation_id="corr-parent-child",
        job=lambda: "child-result",
    )

    child_final = repository.saved[-1]

    assert parent_result == "parent-result"
    assert child_result == "child-result"

    assert parent_final.status == "completed"
    assert parent_final.parent_job_run_id is None

    assert child_final.status == "completed"
    assert child_final.parent_job_run_id == parent_final.job_run_id
    assert child_final.correlation_id == parent_final.correlation_id

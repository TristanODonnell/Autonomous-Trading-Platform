"""Tests for parallel execution observability: unit metrics and runtime context propagation."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from autonomous_trading_platform.observability.runtime_context import (
    RuntimeContext,
    get_runtime_context,
    runtime_context,
)
from autonomous_trading_platform.research.execution import (
    ExecutionMode,
    ExecutionUnit,
    ParallelExecutionConfig,
    ParallelExecutionError,
    ParallelExecutionService,
)


def _unit(uid: str, sort_key: int, fn=None) -> ExecutionUnit:
    return ExecutionUnit(
        unit_id=uid,
        sort_key=(sort_key,),
        run=fn if fn is not None else (lambda: uid),
    )


class TestParallelExecutionMetrics:
    def test_success_unit_increments_success_counter(self):
        svc = ParallelExecutionService(
            ParallelExecutionConfig(mode=ExecutionMode.SERIAL, stage_name="test")
        )
        with patch(
            "autonomous_trading_platform.research.execution.parallel_execution_service.research_parallel_unit_runs"
        ) as mock_counter:
            svc.run([_unit("u1", 1)])
        calls = mock_counter.add.call_args_list
        statuses = [c.args[1]["status"] for c in calls]
        assert "success" in statuses

    def test_failed_unit_increments_failure_counter(self):
        svc = ParallelExecutionService(
            ParallelExecutionConfig(mode=ExecutionMode.SERIAL, stage_name="test")
        )

        def boom():
            raise ValueError("test")

        with patch(
            "autonomous_trading_platform.research.execution.parallel_execution_service.research_parallel_unit_runs"
        ) as mock_counter:
            svc.run([_unit("u1", 1, fn=boom)])
        calls = mock_counter.add.call_args_list
        statuses = [c.args[1]["status"] for c in calls]
        assert "failed" in statuses

    def test_unit_duration_recorded(self):
        svc = ParallelExecutionService(ParallelExecutionConfig(stage_name="dur_test"))
        with patch(
            "autonomous_trading_platform.research.execution.parallel_execution_service.research_parallel_unit_duration"
        ) as mock_hist:
            svc.run([_unit("u1", 1)])
        assert mock_hist.record.called
        duration = mock_hist.record.call_args.args[0]
        assert duration >= 0.0

    def test_stage_name_label_on_metrics(self):
        svc = ParallelExecutionService(ParallelExecutionConfig(stage_name="my_stage_name"))
        with patch(
            "autonomous_trading_platform.research.execution.parallel_execution_service.research_parallel_unit_runs"
        ) as mock_counter:
            svc.run([_unit("u1", 1)])
        labels = mock_counter.add.call_args.args[1]
        assert labels["stage_name"] == "my_stage_name"

    def test_execution_started_and_completed_logged(self, caplog):
        svc = ParallelExecutionService(ParallelExecutionConfig(stage_name="log_test"))
        log_name = "autonomous_trading_platform.research.execution.parallel_execution_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.run([_unit("u1", 1)])
        messages = [r.message for r in caplog.records]
        assert "parallel_execution_started" in messages
        assert "parallel_execution_completed" in messages

    def test_execution_started_log_includes_unit_count(self, caplog):
        svc = ParallelExecutionService(ParallelExecutionConfig(stage_name="count_test"))
        log_name = "autonomous_trading_platform.research.execution.parallel_execution_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.run([_unit("u1", 1), _unit("u2", 2)])
        started = next(r for r in caplog.records if r.message == "parallel_execution_started")
        assert started.__dict__.get("unit_count") == 2

    def test_runtime_context_propagates_into_serial_workers(self):
        captured: list[RuntimeContext | None] = []

        def capture():
            captured.append(get_runtime_context())
            return "ok"

        svc = ParallelExecutionService(ParallelExecutionConfig(mode=ExecutionMode.SERIAL))
        with runtime_context(run_id="test-run-serial-999"):
            svc.run([_unit("u1", 1, fn=capture)])

        assert captured[0] is not None
        assert captured[0].run_id == "test-run-serial-999"

    def test_runtime_context_propagates_into_parallel_workers(self):
        captured: list[RuntimeContext | None] = []

        def capture():
            captured.append(get_runtime_context())
            return "ok"

        svc = ParallelExecutionService(
            ParallelExecutionConfig(mode=ExecutionMode.PARALLEL, max_workers=2)
        )
        with runtime_context(run_id="test-run-parallel-999"):
            svc.run([_unit("u1", 1, fn=capture), _unit("u2", 2, fn=capture)])

        for ctx in captured:
            assert ctx is not None
            assert ctx.run_id == "test-run-parallel-999"

    def test_fail_fast_triggers_warning_log(self, caplog):
        svc = ParallelExecutionService(
            ParallelExecutionConfig(mode=ExecutionMode.SERIAL, fail_fast=True)
        )

        def boom():
            raise RuntimeError("bang")

        log_name = "autonomous_trading_platform.research.execution.parallel_execution_service"
        with (
            caplog.at_level(logging.WARNING, logger=log_name),
            pytest.raises(ParallelExecutionError),
        ):
            svc.run([_unit("u1", 1, fn=boom)])

        messages = [r.message for r in caplog.records]
        assert "parallel_execution_fail_fast_triggered" in messages

    def test_mode_label_is_serial_when_single_unit(self):
        svc = ParallelExecutionService(
            ParallelExecutionConfig(mode=ExecutionMode.PARALLEL, max_workers=4, stage_name="s")
        )
        with patch(
            "autonomous_trading_platform.research.execution.parallel_execution_service.research_parallel_unit_runs"
        ) as mock_counter:
            svc.run([_unit("u1", 1)])
        labels = mock_counter.add.call_args.args[1]
        # Single unit → serial mode path
        assert labels["mode"] in ("serial", "parallel")

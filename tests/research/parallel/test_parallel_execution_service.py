import time

import pytest

from autonomous_trading_platform.research.execution import (
    ExecutionMode,
    ExecutionUnit,
    ParallelExecutionConfig,
    ParallelExecutionError,
    ParallelExecutionService,
)


def test_parallel_results_are_returned_in_sort_key_order() -> None:
    service = ParallelExecutionService(
        ParallelExecutionConfig(mode=ExecutionMode.PARALLEL, max_workers=3)
    )

    def slow() -> str:
        time.sleep(0.03)
        return "slow"

    units = [
        ExecutionUnit(unit_id="slow", sort_key=(2,), run=slow),
        ExecutionUnit(unit_id="fast", sort_key=(1,), run=lambda: "fast"),
    ]

    results = service.values(units)

    assert results == ["fast", "slow"]


def test_collect_errors_returns_all_failures_before_raise() -> None:
    service = ParallelExecutionService(
        ParallelExecutionConfig(mode=ExecutionMode.PARALLEL, max_workers=2, fail_fast=False)
    )

    def fail(message: str):
        raise RuntimeError(message)

    units = [
        ExecutionUnit(unit_id="a", sort_key=(1,), run=lambda: fail("first")),
        ExecutionUnit(unit_id="b", sort_key=(2,), run=lambda: fail("second")),
    ]

    with pytest.raises(ParallelExecutionError) as exc_info:
        service.values(units)

    assert [failure.unit_id for failure in exc_info.value.failures] == ["a", "b"]


def test_serial_and_parallel_have_same_logical_order() -> None:
    units = [
        ExecutionUnit(unit_id="b", sort_key=(2,), run=lambda: "b"),
        ExecutionUnit(unit_id="a", sort_key=(1,), run=lambda: "a"),
    ]

    serial_values = ParallelExecutionService(
        ParallelExecutionConfig(mode=ExecutionMode.SERIAL)
    ).values(units)
    parallel_values = ParallelExecutionService(
        ParallelExecutionConfig(mode=ExecutionMode.PARALLEL, max_workers=2)
    ).values(units)

    assert serial_values == parallel_values == ["a", "b"]

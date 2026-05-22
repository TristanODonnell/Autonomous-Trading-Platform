from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from autonomous_trading_platform.research.execution.execution_result import (
    ExecutionFailure,
    ExecutionResult,
    ParallelExecutionError,
)
from autonomous_trading_platform.research.execution.execution_unit import ExecutionUnit
from autonomous_trading_platform.research.execution.result_ordering import (
    order_results,
    order_units,
)

T = TypeVar("T")


class ExecutionMode(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class ParallelExecutionConfig:
    mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if self.max_workers < 1:
            raise ValueError("max_workers must be >= 1")


class ParallelExecutionService:
    """Local deterministic executor for research-only independent units.

    ThreadPoolExecutor is used deliberately: current simulation runners often
    carry repository/session objects and are not safely pickleable for process
    execution. Parallel mode is opt-in; serial mode remains the default.
    """

    def __init__(self, config: ParallelExecutionConfig | None = None) -> None:
        self._config = config or ParallelExecutionConfig()

    def run(self, units: list[ExecutionUnit[T]]) -> list[ExecutionResult[T]]:
        ordered_units = order_units(units)
        if not ordered_units:
            return []
        if (
            self._config.mode == ExecutionMode.SERIAL
            or self._config.max_workers == 1
            or len(ordered_units) == 1
        ):
            return self._run_serial(ordered_units)
        return self._run_parallel(ordered_units)

    def values(self, units: list[ExecutionUnit[T]]) -> list[T]:
        results = self.run(units)
        failures = [result.failure for result in results if result.failure is not None]
        if failures:
            raise ParallelExecutionError(failures)
        return [result.value for result in results if result.value is not None]

    def _run_serial(self, units: list[ExecutionUnit[T]]) -> list[ExecutionResult[T]]:
        results: list[ExecutionResult[T]] = []
        failures: list[ExecutionFailure] = []
        for unit in units:
            result = self._execute_unit(unit)
            results.append(result)
            if result.failure is not None:
                failures.append(result.failure)
                if self._config.fail_fast:
                    raise ParallelExecutionError(failures)
        return order_results(results)

    def _run_parallel(self, units: list[ExecutionUnit[T]]) -> list[ExecutionResult[T]]:
        results: list[ExecutionResult[T]] = []
        futures: dict[Future[ExecutionResult[T]], ExecutionUnit[T]] = {}
        with ThreadPoolExecutor(max_workers=self._config.max_workers) as executor:
            for unit in units:
                futures[executor.submit(self._execute_unit, unit)] = unit

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if self._config.fail_fast and result.failure is not None:
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
                    raise ParallelExecutionError([result.failure])

        return order_results(results)

    @staticmethod
    def _execute_unit(unit: ExecutionUnit[T]) -> ExecutionResult[T]:
        try:
            return ExecutionResult(
                unit_id=unit.unit_id,
                sort_key=unit.sort_key,
                value=unit.run(),
                metadata=unit.metadata,
            )
        except Exception as exc:
            return ExecutionResult(
                unit_id=unit.unit_id,
                sort_key=unit.sort_key,
                failure=ExecutionFailure(
                    unit_id=unit.unit_id,
                    sort_key=unit.sort_key,
                    error_type=exc.__class__.__name__,
                    error_message=str(exc),
                    metadata=unit.metadata,
                ),
                metadata=unit.metadata,
            )

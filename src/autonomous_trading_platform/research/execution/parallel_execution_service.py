from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from autonomous_trading_platform.observability.log_context import LogContext
from autonomous_trading_platform.observability.metric_labels import observability_environment
from autonomous_trading_platform.observability.metrics import (
    research_parallel_unit_duration,
    research_parallel_unit_runs,
)
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

logger = logging.getLogger(__name__)

T = TypeVar("T")

_COMPONENT = "research.parallel_execution"


class ExecutionMode(StrEnum):
    SERIAL = "serial"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class ParallelExecutionConfig:
    mode: ExecutionMode = ExecutionMode.SERIAL
    max_workers: int = 1
    fail_fast: bool = False
    stage_name: str = "unknown"

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

        use_parallel = (
            self._config.mode == ExecutionMode.PARALLEL
            and self._config.max_workers > 1
            and len(ordered_units) > 1
        )
        effective_mode = ExecutionMode.PARALLEL if use_parallel else ExecutionMode.SERIAL

        logger.info(
            "parallel_execution_started",
            extra={
                **LogContext(component=_COMPONENT).to_extra(),
                "mode": effective_mode.value,
                "max_workers": self._config.max_workers,
                "unit_count": len(ordered_units),
                "stage_name": self._config.stage_name,
            },
        )

        results = (
            self._run_parallel(ordered_units) if use_parallel else self._run_serial(ordered_units)
        )

        failure_count = sum(1 for r in results if r.failure is not None)
        logger.info(
            "parallel_execution_completed",
            extra={
                **LogContext(component=_COMPONENT).to_extra(),
                "mode": effective_mode.value,
                "unit_count": len(ordered_units),
                "success_count": len(ordered_units) - failure_count,
                "failure_count": failure_count,
                "stage_name": self._config.stage_name,
            },
        )

        return results

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
                    logger.warning(
                        "parallel_execution_fail_fast_triggered",
                        extra={
                            **LogContext(component=_COMPONENT).to_extra(),
                            "unit_id": unit.unit_id,
                            "stage_name": self._config.stage_name,
                            "error_type": result.failure.error_type,
                        },
                    )
                    raise ParallelExecutionError(failures)
        return order_results(results)

    def _run_parallel(self, units: list[ExecutionUnit[T]]) -> list[ExecutionResult[T]]:
        # Each unit gets its own context copy: a single Context object can only be
        # entered once at a time, so sharing it across threads would fail.
        per_unit_ctxs = [contextvars.copy_context() for _ in units]
        results: list[ExecutionResult[T]] = []
        futures: dict[Future[ExecutionResult[T]], ExecutionUnit[T]] = {}
        with ThreadPoolExecutor(max_workers=self._config.max_workers) as executor:
            for ctx_copy, unit in zip(per_unit_ctxs, units, strict=True):
                futures[executor.submit(ctx_copy.run, self._execute_unit, unit)] = unit

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if self._config.fail_fast and result.failure is not None:
                    unit = futures[future]
                    logger.warning(
                        "parallel_execution_fail_fast_triggered",
                        extra={
                            **LogContext(component=_COMPONENT).to_extra(),
                            "unit_id": unit.unit_id,
                            "stage_name": self._config.stage_name,
                            "error_type": result.failure.error_type,
                        },
                    )
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
                    raise ParallelExecutionError([result.failure])

        return order_results(results)

    def _execute_unit(self, unit: ExecutionUnit[T]) -> ExecutionResult[T]:
        env = observability_environment()
        labels = {
            "environment": env,
            "stage_name": self._config.stage_name,
            "mode": self._config.mode.value,
        }
        t0 = time.perf_counter()
        try:
            value = unit.run()
            duration = time.perf_counter() - t0
            research_parallel_unit_runs.add(1, {**labels, "status": "success"})
            research_parallel_unit_duration.record(duration, labels)
            return ExecutionResult(
                unit_id=unit.unit_id,
                sort_key=unit.sort_key,
                value=value,
                metadata=unit.metadata,
            )
        except Exception as exc:
            duration = time.perf_counter() - t0
            research_parallel_unit_runs.add(1, {**labels, "status": "failed"})
            research_parallel_unit_duration.record(duration, labels)
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

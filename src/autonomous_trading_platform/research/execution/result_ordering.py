from __future__ import annotations

from typing import TypeVar

from autonomous_trading_platform.research.execution.execution_result import ExecutionResult
from autonomous_trading_platform.research.execution.execution_unit import ExecutionUnit

T = TypeVar("T")


def order_units(units: list[ExecutionUnit[T]]) -> list[ExecutionUnit[T]]:
    return sorted(units, key=lambda unit: unit.sort_key)


def order_results(results: list[ExecutionResult[T]]) -> list[ExecutionResult[T]]:
    return sorted(results, key=lambda result: result.sort_key)

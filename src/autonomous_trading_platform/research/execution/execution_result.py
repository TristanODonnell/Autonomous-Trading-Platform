from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ExecutionFailure:
    unit_id: str
    sort_key: tuple[Any, ...]
    error_type: str
    error_message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult(Generic[T]):
    unit_id: str
    sort_key: tuple[Any, ...]
    value: T | None = None
    failure: ExecutionFailure | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.failure is None


class ParallelExecutionError(RuntimeError):
    def __init__(self, failures: list[ExecutionFailure]) -> None:
        self.failures = sorted(failures, key=lambda failure: failure.sort_key)
        summary = "; ".join(
            f"{failure.unit_id}: {failure.error_type}: {failure.error_message}"
            for failure in self.failures
        )
        super().__init__(f"{len(self.failures)} research execution unit(s) failed: {summary}")

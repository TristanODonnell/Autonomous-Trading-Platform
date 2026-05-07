from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.scheduler.registry.no_overlap_lock import (
    InMemoryNoOverlapLock,
)
from autonomous_trading_platform.scheduler.registry.scheduler_registry import (
    SCHEDULER_REGISTRY,
)


@dataclass(frozen=True)
class ManualTriggerResult:
    job_name: str
    status: str
    result: Any = None


class ManualTriggerService:
    def __init__(
        self,
        dispatchers: dict[str, Callable[[], Any]],
        lock: InMemoryNoOverlapLock | None = None,
    ):
        self._dispatchers = dispatchers
        self._lock = lock or InMemoryNoOverlapLock()

    def trigger(self, job_name: str) -> ManualTriggerResult:
        definition = SCHEDULER_REGISTRY.get(job_name)

        if definition is None:
            raise ValueError(f"Unknown scheduler job: {job_name}")

        if not definition.manual_trigger_enabled:
            raise ValueError(f"Manual trigger disabled for scheduler job: {job_name}")

        dispatcher = self._dispatchers.get(job_name)

        if dispatcher is None:
            raise ValueError(f"No dispatcher registered for scheduler job: {job_name}")

        acquired = self._lock.acquire(definition.lock_key)

        if not acquired:
            return ManualTriggerResult(
                job_name=job_name,
                status="skipped",
                result={"reason": "already_running"},
            )

        try:
            result = dispatcher()

            return ManualTriggerResult(
                job_name=job_name,
                status="completed",
                result=result,
            )
        finally:
            self._lock.release(definition.lock_key)

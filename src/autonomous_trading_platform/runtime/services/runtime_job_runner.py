from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import uuid4

from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.observability.runtime_context import (
    get_runtime_context,
    runtime_context,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


class RuntimeJobRunWriter(Protocol):
    def save(self, contract: RuntimeJobRun) -> RuntimeJobRun: ...


class RuntimeJobFailureNotifier(Protocol):
    def notify_failure(self, failed_run: RuntimeJobRun) -> None: ...


class RuntimeJobRunner:
    def __init__(
        self,
        repository: RuntimeJobRunWriter,
        failure_notifier: RuntimeJobFailureNotifier | None = None,
    ) -> None:
        self.repository = repository
        self.failure_notifier = failure_notifier

    def run(
        self,
        *,
        job_name: str,
        trigger_type: str,
        job: Callable[[], T],
        parent_job_run_id: str | None = None,
        correlation_id: str | None = None,
        input_summary_json: dict[str, object] | None = None,
        skip_reason: str | None = None,
        output_summary_json: Callable[[T], dict[str, object] | None] | None = None,
    ) -> T | None:
        # Pull from ambient context when not explicitly provided.
        ctx = get_runtime_context()
        if ctx is not None:
            if parent_job_run_id is None:
                parent_job_run_id = ctx.job_run_id
            if correlation_id is None:
                correlation_id = ctx.correlation_id

        job_run_id = str(uuid4())
        started_at = datetime.now(UTC)

        if skip_reason is not None:
            completed_at = datetime.now(UTC)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            self.repository.save(
                RuntimeJobRun(
                    job_run_id=job_run_id,
                    job_name=job_name,
                    parent_job_run_id=parent_job_run_id,
                    status="skipped",
                    trigger_type=trigger_type,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    error_message=None,
                    correlation_id=correlation_id,
                    input_summary_json=input_summary_json,
                    output_summary_json={"skip_reason": skip_reason},
                )
            )

            return None

        self.repository.save(
            RuntimeJobRun(
                job_run_id=job_run_id,
                job_name=job_name,
                parent_job_run_id=parent_job_run_id,
                status="running",
                trigger_type=trigger_type,
                started_at=started_at,
                completed_at=None,
                duration_ms=None,
                error_message=None,
                correlation_id=correlation_id,
                input_summary_json=input_summary_json,
                output_summary_json=None,
            )
        )

        try:
            # Bind job identity into context so child jobs see this run as their parent.
            with runtime_context(
                job_run_id=job_run_id,
                job_name=job_name,
                correlation_id=correlation_id,
            ):
                result = job()
        except Exception as exc:
            completed_at = datetime.now(UTC)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            failed_run = self.repository.save(
                RuntimeJobRun(
                    job_run_id=job_run_id,
                    job_name=job_name,
                    parent_job_run_id=parent_job_run_id,
                    status="failed",
                    trigger_type=trigger_type,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    error_message=str(exc),
                    correlation_id=correlation_id,
                    input_summary_json=input_summary_json,
                    output_summary_json=None,
                )
            )
            self._notify_failure(failed_run)

            raise

        completed_at = datetime.now(UTC)
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        self.repository.save(
            RuntimeJobRun(
                job_run_id=job_run_id,
                job_name=job_name,
                parent_job_run_id=parent_job_run_id,
                status="completed",
                trigger_type=trigger_type,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                error_message=None,
                correlation_id=correlation_id,
                input_summary_json=input_summary_json,
                output_summary_json=(
                    output_summary_json(result) if output_summary_json is not None else None
                ),
            )
        )

        return result

    def _notify_failure(self, failed_run: RuntimeJobRun) -> None:
        if self.failure_notifier is None:
            return

        try:
            self.failure_notifier.notify_failure(failed_run)
        except Exception:
            logger.warning(
                "runtime_job.failure_notification_failed",
                extra={
                    "job_run_id": failed_run.job_run_id,
                    "job_name": failed_run.job_name,
                },
                exc_info=True,
            )

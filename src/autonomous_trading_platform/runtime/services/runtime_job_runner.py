from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar
from uuid import uuid4

from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun

T = TypeVar("T")


class RuntimeJobRunWriter(Protocol):
    def save(self, contract: RuntimeJobRun) -> RuntimeJobRun: ...


class RuntimeJobRunner:
    def __init__(self, repository: RuntimeJobRunWriter) -> None:
        self.repository = repository

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
    ) -> T | None:
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
            result = job()
        except Exception as exc:
            completed_at = datetime.now(UTC)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            self.repository.save(
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
                output_summary_json=None,
            )
        )

        return result

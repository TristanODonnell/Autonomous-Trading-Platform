from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.observability.correlation_links import loki_link, tempo_link
from autonomous_trading_platform.storage.sor.repositories.queries.operations_repository import (
    OperationsRepository,
)


class OperationsService:
    def __init__(
        self,
        session: Session,
        repository: OperationsRepository | None = None,
    ) -> None:
        self.repository = repository or OperationsRepository(session)

    def list_jobs(self) -> list[dict[str, Any]]:
        return [
            {
                "job_name": row.job_name,
                "latest_status": row.latest_status,
                "last_started_at": row.last_started_at,
                "last_completed_at": row.last_completed_at,
                "last_duration_ms": row.last_duration_ms,
                "last_error_message": row.last_error_message,
                "run_count": row.run_count,
            }
            for row in self.repository.list_jobs()
        ]

    def list_job_runs(
        self,
        *,
        job_name: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return [
            {
                "job_run_id": row.job_run_id,
                "job_name": row.job_name,
                "parent_job_run_id": row.parent_job_run_id,
                "status": row.status,
                "trigger_type": row.trigger_type,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "duration_ms": row.duration_ms,
                "error_message": row.error_message,
                "correlation_id": row.correlation_id,
                "tempo_link": tempo_link(row.correlation_id),
                "loki_link": loki_link(row.correlation_id),
                "input_summary": row.input_summary_json,
                "output_summary": row.output_summary_json,
            }
            for row in self.repository.list_job_runs(job_name=job_name, limit=limit)
        ]

    def get_runtime_state(self) -> dict[str, Any]:
        state = self.repository.get_runtime_state()
        return {
            "trading_enabled": state.trading_enabled,
            "trading_paused": state.trading_paused,
            "kill_switch_enabled": state.kill_switch_enabled,
            "trading_mode": state.trading_mode,
            "reason": state.reason,
            "updated_by": state.updated_by,
            "updated_at": state.updated_at,
        }

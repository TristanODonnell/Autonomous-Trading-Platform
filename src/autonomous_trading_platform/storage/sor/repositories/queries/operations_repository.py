from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core import (
    runtime_control_state_repository,
)

RuntimeControlStateRepository = runtime_control_state_repository.RuntimeControlStateRepository


@dataclass(frozen=True)
class OperationsJobSummary:
    job_name: str
    latest_status: str
    last_started_at: datetime
    last_completed_at: datetime | None
    last_duration_ms: int | None
    last_error_message: str | None
    run_count: int


@dataclass(frozen=True)
class OperationsRuntimeState:
    trading_enabled: bool
    trading_paused: bool
    kill_switch_enabled: bool
    trading_mode: str
    reason: str | None
    updated_by: str | None
    updated_at: datetime


class OperationsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.runtime_control_state_repository = RuntimeControlStateRepository(session)

    def list_jobs(self) -> list[OperationsJobSummary]:
        latest_runs = self._list_latest_runs_by_job_name()
        run_counts = self._count_runs_by_job_name()

        return [
            OperationsJobSummary(
                job_name=row.job_name,
                latest_status=row.status,
                last_started_at=row.started_at,
                last_completed_at=row.completed_at,
                last_duration_ms=row.duration_ms,
                last_error_message=row.error_message,
                run_count=run_counts.get(row.job_name, 0),
            )
            for row in latest_runs
        ]

    def list_job_runs(
        self,
        *,
        job_name: str,
        limit: int = 20,
    ) -> list[RuntimeJobRuns]:
        safe_limit = min(max(limit, 1), 100)
        stmt = (
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.job_name == job_name)
            .order_by(desc(RuntimeJobRuns.started_at))
            .limit(safe_limit)
        )
        return list(self.session.scalars(stmt).all())

    def get_runtime_state(self) -> OperationsRuntimeState:
        state = self.runtime_control_state_repository.get_or_create_global_state()
        return self._to_runtime_state(state)

    def _list_latest_runs_by_job_name(self) -> list[RuntimeJobRuns]:
        rows = list(
            self.session.scalars(
                select(RuntimeJobRuns).order_by(desc(RuntimeJobRuns.started_at))
            ).all()
        )

        latest_by_job_name: dict[str, RuntimeJobRuns] = {}
        for row in rows:
            latest_by_job_name.setdefault(row.job_name, row)

        return list(latest_by_job_name.values())

    def _count_runs_by_job_name(self) -> dict[str, int]:
        stmt = select(RuntimeJobRuns.job_name, func.count(RuntimeJobRuns.job_run_id)).group_by(
            RuntimeJobRuns.job_name
        )
        return {str(job_name): int(count) for job_name, count in self.session.execute(stmt).all()}

    def _to_runtime_state(self, state: RuntimeControlState) -> OperationsRuntimeState:
        return OperationsRuntimeState(
            trading_enabled=state.trading_enabled,
            trading_paused=state.trading_paused,
            kill_switch_enabled=state.kill_switch_enabled,
            trading_mode=state.trading_mode,
            reason=state.reason,
            updated_by=state.updated_by,
            updated_at=state.updated_at,
        )

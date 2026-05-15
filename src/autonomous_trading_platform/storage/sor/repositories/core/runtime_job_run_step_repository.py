from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.runtime_job_run_step import RuntimeJobRunStep
from autonomous_trading_platform.storage.sor.models.runtime_job_run_steps import RuntimeJobRunSteps
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class RuntimeJobRunStepRepository(BaseRepository):
    def append(self, step: RuntimeJobRunStep) -> RuntimeJobRunStep:
        row = self._to_row(step)
        self.session.add(row)
        self.session.flush()
        return step

    def list_by_job_run_id(self, *, job_run_id: str) -> list[RuntimeJobRunSteps]:
        stmt = (
            select(RuntimeJobRunSteps)
            .where(RuntimeJobRunSteps.job_run_id == job_run_id)
            .order_by(RuntimeJobRunSteps.sequence_number.asc())
        )
        return list(self.session.scalars(stmt).all())

    def _to_row(self, step: RuntimeJobRunStep) -> RuntimeJobRunSteps:
        return RuntimeJobRunSteps(
            step_id=step.step_id,
            job_run_id=step.job_run_id,
            step_name=step.step_name,
            status=step.status,
            sequence_number=step.sequence_number,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_ms=step.duration_ms,
            error_message=step.error_message,
            error_type=step.error_type,
        )

    def to_contract(self, row: RuntimeJobRunSteps) -> RuntimeJobRunStep:
        return RuntimeJobRunStep(
            step_id=UUID(str(row.step_id)),
            job_run_id=row.job_run_id,
            step_name=row.step_name,
            status=row.status,
            sequence_number=row.sequence_number,
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            error_message=row.error_message,
            error_type=row.error_type,
        )

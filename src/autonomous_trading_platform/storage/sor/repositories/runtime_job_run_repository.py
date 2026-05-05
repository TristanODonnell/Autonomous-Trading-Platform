from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.runtime_job_run import RuntimeJobRun
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class RuntimeJobRunRepository(BaseRepository):
    def get_by_job_run_id(
        self,
        job_run_id: str,
    ) -> RuntimeJobRuns | None:
        stmt = select(RuntimeJobRuns).where(RuntimeJobRuns.job_run_id == job_run_id)
        return cast(
            RuntimeJobRuns | None,
            self.session.scalars(stmt).one_or_none(),
        )

    def insert(self, row: RuntimeJobRuns) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[RuntimeJobRuns]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: RuntimeJobRuns) -> RuntimeJobRuns:
        existing = self.get_by_job_run_id(row.job_run_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in RuntimeJobRuns.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_job_run_id(self, job_run_id: str) -> None:
        obj = self.get_by_job_run_id(job_run_id)
        if obj is not None:
            self.session.delete(obj)

    def list_by_job_name(self, *, job_name: str) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.job_name == job_name)
            .order_by(RuntimeJobRuns.started_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_correlation_id(self, *, correlation_id: str) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.correlation_id == correlation_id)
            .order_by(RuntimeJobRuns.started_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def list_children(self, *, parent_job_run_id: str) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(RuntimeJobRuns.parent_job_run_id == parent_job_run_id)
            .order_by(RuntimeJobRuns.started_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def to_row(self, contract: RuntimeJobRun) -> RuntimeJobRuns:
        return RuntimeJobRuns(
            job_run_id=contract.job_run_id,
            job_name=contract.job_name,
            parent_job_run_id=contract.parent_job_run_id,
            status=contract.status,
            trigger_type=contract.trigger_type,
            started_at=contract.started_at,
            completed_at=contract.completed_at,
            duration_ms=contract.duration_ms,
            error_message=contract.error_message,
            correlation_id=contract.correlation_id,
            input_summary_json=contract.input_summary_json,
            output_summary_json=contract.output_summary_json,
        )

    def to_contract(self, row: RuntimeJobRuns) -> RuntimeJobRun:
        return RuntimeJobRun(
            job_run_id=row.job_run_id,
            job_name=row.job_name,
            parent_job_run_id=row.parent_job_run_id,
            status=row.status,
            trigger_type=row.trigger_type,
            started_at=row.started_at,
            completed_at=row.completed_at,
            duration_ms=row.duration_ms,
            error_message=row.error_message,
            correlation_id=row.correlation_id,
            input_summary_json=row.input_summary_json,
            output_summary_json=row.output_summary_json,
        )

    def save(self, contract: RuntimeJobRun) -> RuntimeJobRun:
        row = self.to_row(contract)
        saved = self.upsert(row)
        self.session.flush()

        return self.to_contract(saved)

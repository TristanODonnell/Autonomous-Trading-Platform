from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.ingestion_run import IngestionRun
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class IngestionRunsRepository(BaseRepository):
    def get_by_ingestion_run_id(self, ingestion_run_id: str) -> IngestionRuns | None:
        stmt = select(IngestionRuns).where(IngestionRuns.ingestion_run_id == ingestion_run_id)
        return cast(IngestionRuns | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: IngestionRuns) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[IngestionRuns]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: IngestionRuns) -> IngestionRuns:
        existing = self.get_by_ingestion_run_id(row.ingestion_run_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in IngestionRuns.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_ingestion_run_id(self, ingestion_run_id: str) -> None:
        obj = self.get_by_ingestion_run_id(ingestion_run_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: IngestionRun) -> IngestionRuns:
        return IngestionRuns(
            ingestion_run_id=contract.ingestion_run_id,
            created_at=contract.created_at,
            run_timestamp=contract.run_timestamp,
            run_type=contract.run_type,
            source=contract.source,
            dataset_version=contract.dataset_version,
            status=contract.status,
            started_at=contract.started_at,
            completed_at=contract.completed_at,
            error_message=contract.error_message,
            row_count=contract.row_count,
            file_count=contract.file_count,
        )

    def to_contract(self, row: IngestionRuns) -> IngestionRun:
        return IngestionRun(
            ingestion_run_id=row.ingestion_run_id,
            created_at=row.created_at,
            run_timestamp=row.run_timestamp,
            run_type=row.run_type,
            source=row.source,
            dataset_version=row.dataset_version,
            status=row.status,
            started_at=row.started_at,
            completed_at=row.completed_at,
            error_message=row.error_message,
            row_count=row.row_count,
            file_count=row.file_count,
        )

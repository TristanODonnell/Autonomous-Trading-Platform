from typing import cast

from sqlalchemy import select

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

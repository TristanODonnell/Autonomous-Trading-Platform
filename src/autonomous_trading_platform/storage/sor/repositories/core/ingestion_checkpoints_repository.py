from datetime import date
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import CheckpointScope, CheckpointStatus
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.storage.sor.models.ingestion_checkpoint import (
    IngestionCheckpoint,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class IngestionCheckpointsRepository(BaseRepository):
    def get_by_checkpoint_id(self, checkpoint_id: str) -> IngestionCheckpoint | None:
        stmt = select(IngestionCheckpoint).where(IngestionCheckpoint.checkpoint_id == checkpoint_id)
        return cast(IngestionCheckpoint | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: IngestionCheckpoint) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[IngestionCheckpoint]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: IngestionCheckpoint) -> IngestionCheckpoint:
        existing = self.get_by_checkpoint_id(row.checkpoint_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in IngestionCheckpoint.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_checkpoint_id(self, checkpoint_id: str) -> None:
        obj = self.get_by_checkpoint_id(checkpoint_id)
        if obj is not None:
            self.session.delete(obj)

    def list_by_ingestion_run_id(self, ingestion_run_id: str) -> list[IngestionCheckpoint]:
        stmt = (
            select(IngestionCheckpoint)
            .where(IngestionCheckpoint.ingestion_run_id == ingestion_run_id)
            .order_by(IngestionCheckpoint.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_incomplete_by_ingestion_run_id(
        self,
        ingestion_run_id: str,
    ) -> list[IngestionCheckpoint]:
        stmt = (
            select(IngestionCheckpoint)
            .where(
                IngestionCheckpoint.ingestion_run_id == ingestion_run_id,
                IngestionCheckpoint.checkpoint_status.in_(
                    [
                        CheckpointStatus.PENDING,
                        CheckpointStatus.IN_PROGRESS,
                        CheckpointStatus.FAILED,
                    ]
                ),
            )
            .order_by(IngestionCheckpoint.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_backfill_checkpoint(
        self,
        *,
        ingestion_run_id: str,
        dataset_version: str,
        symbol: str,
        checkpoint_date: date,
    ) -> IngestionCheckpoint | None:
        stmt = select(IngestionCheckpoint).where(
            IngestionCheckpoint.ingestion_run_id == ingestion_run_id,
            IngestionCheckpoint.dataset_version == dataset_version,
            IngestionCheckpoint.symbol == symbol,
            IngestionCheckpoint.checkpoint_date == checkpoint_date,
        )
        return cast(IngestionCheckpoint | None, self.session.scalars(stmt).one_or_none())

    def get_cycle_checkpoint(
        self,
        *,
        ingestion_run_id: str,
        dataset_version: str,
        cycle_timestamp: UTCDateTime,
    ) -> IngestionCheckpoint | None:
        stmt = select(IngestionCheckpoint).where(
            IngestionCheckpoint.ingestion_run_id == ingestion_run_id,
            IngestionCheckpoint.dataset_version == dataset_version,
            IngestionCheckpoint.cycle_timestamp == cycle_timestamp,
            IngestionCheckpoint.checkpoint_scope == CheckpointScope.INCREMENTAL,
        )
        return cast(IngestionCheckpoint | None, self.session.scalars(stmt).one_or_none())

    def list_for_run_and_dataset(
        self,
        *,
        ingestion_run_id: str,
        dataset_version: str,
    ) -> list[IngestionCheckpoint]:
        stmt = (
            select(IngestionCheckpoint)
            .where(
                IngestionCheckpoint.ingestion_run_id == ingestion_run_id,
                IngestionCheckpoint.dataset_version == dataset_version,
            )
            .order_by(IngestionCheckpoint.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

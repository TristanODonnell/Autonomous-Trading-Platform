# autonomous_trading_platform/storage/sor/repositories/fill_quality_metrics_repository.py
from __future__ import annotations

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class FillQualityMetricsRepository(BaseRepository):
    def get_by_record_id(self, record_id: str) -> FillQualityMetrics | None:
        stmt = select(FillQualityMetrics).where(FillQualityMetrics.record_id == record_id)
        return cast(FillQualityMetrics | None, self.session.scalars(stmt).one_or_none())

    def get_by_intent_id(self, intent_id: str) -> FillQualityMetrics | None:
        stmt = select(FillQualityMetrics).where(FillQualityMetrics.intent_id == intent_id)
        return cast(FillQualityMetrics | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: FillQualityMetrics) -> None:
        self.session.add(row)

    def upsert(self, row: FillQualityMetrics) -> FillQualityMetrics:
        existing = self.get_by_record_id(row.record_id)
        if existing is None:
            self.session.add(row)
            return row
        for column in FillQualityMetrics.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))
        return existing

    def upsert_by_intent_id(self, row: FillQualityMetrics) -> FillQualityMetrics:

        existing = self.get_by_intent_id(row.intent_id)
        if existing is None:
            self.session.add(row)
            return row
        for column in FillQualityMetrics.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))
        return existing

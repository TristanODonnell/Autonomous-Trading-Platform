# autonomous_trading_platform/storage/sor/repositories/fill_quality_metrics_repository.py
from __future__ import annotations

from datetime import datetime
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

    def get_for_calibration(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[FillQualityMetrics]:
        """Return completed fills (slippage_bps NOT NULL) in the given time window.

        Rows are ordered by fill_timestamp ascending for deterministic aggregation.
        Only rows with both fill_timestamp and slippage_bps populated are returned —
        partial or pending fills are excluded.
        """
        stmt = (
            select(FillQualityMetrics)
            .where(
                FillQualityMetrics.fill_timestamp.is_not(None),
                FillQualityMetrics.fill_timestamp >= window_start,
                FillQualityMetrics.fill_timestamp <= window_end,
                FillQualityMetrics.slippage_bps.is_not(None),
            )
            .order_by(FillQualityMetrics.fill_timestamp)
        )
        return list(self.session.scalars(stmt).all())

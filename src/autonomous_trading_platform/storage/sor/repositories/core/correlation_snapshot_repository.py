from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.correlation_snapshots import (
    CorrelationSnapshotRow,
    CovarianceSnapshotRow,
)


class CorrelationSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Correlation snapshots
    # ------------------------------------------------------------------

    def insert_correlation(self, row: CorrelationSnapshotRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_latest_correlation(
        self,
        *,
        snapshot_type: str,
        lookback_window: int,
    ) -> CorrelationSnapshotRow | None:
        return cast(
            CorrelationSnapshotRow | None,
            self._session.scalars(
                select(CorrelationSnapshotRow)
                .where(CorrelationSnapshotRow.snapshot_type == snapshot_type)
                .where(CorrelationSnapshotRow.lookback_window == lookback_window)
                .order_by(CorrelationSnapshotRow.computed_at.desc())
                .limit(1)
            ).first(),
        )

    def get_correlation_history(
        self,
        *,
        snapshot_type: str,
        lookback_window: int,
        since: datetime,
        limit: int = 100,
    ) -> list[CorrelationSnapshotRow]:
        return list(
            self._session.scalars(
                select(CorrelationSnapshotRow)
                .where(CorrelationSnapshotRow.snapshot_type == snapshot_type)
                .where(CorrelationSnapshotRow.lookback_window == lookback_window)
                .where(CorrelationSnapshotRow.computed_at >= since)
                .order_by(CorrelationSnapshotRow.computed_at.desc())
                .limit(limit)
            ).all()
        )

    def get_correlation_snapshots_for_run(self, run_id: str) -> list[CorrelationSnapshotRow]:
        return list(
            self._session.scalars(
                select(CorrelationSnapshotRow)
                .where(CorrelationSnapshotRow.run_id == run_id)
                .order_by(CorrelationSnapshotRow.computed_at.asc())
            ).all()
        )

    # ------------------------------------------------------------------
    # Covariance snapshots
    # ------------------------------------------------------------------

    def insert_covariance(self, row: CovarianceSnapshotRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_latest_covariance(
        self,
        *,
        snapshot_type: str,
        lookback_window: int,
    ) -> CovarianceSnapshotRow | None:
        return cast(
            CovarianceSnapshotRow | None,
            self._session.scalars(
                select(CovarianceSnapshotRow)
                .where(CovarianceSnapshotRow.snapshot_type == snapshot_type)
                .where(CovarianceSnapshotRow.lookback_window == lookback_window)
                .order_by(CovarianceSnapshotRow.computed_at.desc())
                .limit(1)
            ).first(),
        )

    def get_covariance_by_snapshot_id(self, snapshot_id: str) -> CovarianceSnapshotRow | None:
        return cast(
            CovarianceSnapshotRow | None,
            self._session.scalars(
                select(CovarianceSnapshotRow).where(
                    CovarianceSnapshotRow.snapshot_id == snapshot_id
                )
            ).first(),
        )

    def get_covariance_history(
        self,
        *,
        snapshot_type: str,
        lookback_window: int,
        since: datetime,
        limit: int = 100,
    ) -> list[CovarianceSnapshotRow]:
        return list(
            self._session.scalars(
                select(CovarianceSnapshotRow)
                .where(CovarianceSnapshotRow.snapshot_type == snapshot_type)
                .where(CovarianceSnapshotRow.lookback_window == lookback_window)
                .where(CovarianceSnapshotRow.computed_at >= since)
                .order_by(CovarianceSnapshotRow.computed_at.desc())
                .limit(limit)
            ).all()
        )

    def get_covariance_snapshots_for_run(self, run_id: str) -> list[CovarianceSnapshotRow]:
        return list(
            self._session.scalars(
                select(CovarianceSnapshotRow)
                .where(CovarianceSnapshotRow.run_id == run_id)
                .order_by(CovarianceSnapshotRow.computed_at.asc())
            ).all()
        )

from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.factor_exposure_snapshots import (
    FactorExposureSnapshotRow,
    PortfolioFactorExposureRow,
    StrategyFactorExposureRow,
)


class FactorExposureSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_snapshot(self, row: FactorExposureSnapshotRow) -> None:
        self._session.add(row)
        self._session.flush()

    def insert_strategy_exposure(self, row: StrategyFactorExposureRow) -> None:
        self._session.add(row)
        self._session.flush()

    def insert_portfolio_exposure(self, row: PortfolioFactorExposureRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_latest_snapshot(
        self,
        *,
        portfolio_id: str | None = None,
        lookback_window: int | None = None,
    ) -> FactorExposureSnapshotRow | None:
        q = select(FactorExposureSnapshotRow)
        if portfolio_id is not None:
            q = q.where(FactorExposureSnapshotRow.portfolio_id == portfolio_id)
        if lookback_window is not None:
            q = q.where(FactorExposureSnapshotRow.lookback_window == lookback_window)
        q = q.order_by(FactorExposureSnapshotRow.computed_at.desc()).limit(1)
        return cast(FactorExposureSnapshotRow | None, self._session.scalars(q).first())

    def get_snapshot_history(
        self,
        *,
        since: datetime,
        portfolio_id: str | None = None,
        lookback_window: int | None = None,
        limit: int = 100,
    ) -> list[FactorExposureSnapshotRow]:
        q = select(FactorExposureSnapshotRow).where(FactorExposureSnapshotRow.computed_at >= since)
        if portfolio_id is not None:
            q = q.where(FactorExposureSnapshotRow.portfolio_id == portfolio_id)
        if lookback_window is not None:
            q = q.where(FactorExposureSnapshotRow.lookback_window == lookback_window)
        q = q.order_by(FactorExposureSnapshotRow.computed_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def get_strategy_history(
        self,
        *,
        strategy_id: str,
        since: datetime,
        lookback_window: int | None = None,
        limit: int = 100,
    ) -> list[StrategyFactorExposureRow]:
        q = (
            select(StrategyFactorExposureRow)
            .where(StrategyFactorExposureRow.strategy_id == strategy_id)
            .where(StrategyFactorExposureRow.computed_at >= since)
        )
        if lookback_window is not None:
            q = q.where(StrategyFactorExposureRow.lookback_window == lookback_window)
        q = q.order_by(StrategyFactorExposureRow.computed_at.desc()).limit(limit)
        return list(self._session.scalars(q).all())

    def get_latest_portfolio_exposure(
        self,
        *,
        portfolio_id: str | None = None,
        lookback_window: int | None = None,
    ) -> PortfolioFactorExposureRow | None:
        q = select(PortfolioFactorExposureRow)
        if portfolio_id is not None:
            q = q.where(PortfolioFactorExposureRow.portfolio_id == portfolio_id)
        if lookback_window is not None:
            q = q.where(PortfolioFactorExposureRow.lookback_window == lookback_window)
        q = q.order_by(PortfolioFactorExposureRow.computed_at.desc()).limit(1)
        return cast(PortfolioFactorExposureRow | None, self._session.scalars(q).first())

    def get_snapshots_for_run(self, run_id: str) -> list[FactorExposureSnapshotRow]:
        return list(
            self._session.scalars(
                select(FactorExposureSnapshotRow)
                .where(FactorExposureSnapshotRow.run_id == run_id)
                .order_by(FactorExposureSnapshotRow.computed_at.asc())
            ).all()
        )

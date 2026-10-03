from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    PerformanceSnapshotBase,
    StrategyLivePerformanceSnapshot,
    StrategyShadowPerformanceSnapshot,
)


class LivePerformanceSnapshotRepository:
    """Live performance snapshots; ShadowPerformanceSnapshotRepository mirrors it for on-deck."""

    model: type[PerformanceSnapshotBase] = StrategyLivePerformanceSnapshot

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_latest(self, strategy_id: str) -> PerformanceSnapshotBase | None:
        model = self.model
        return cast(
            PerformanceSnapshotBase | None,
            self._session.scalars(
                select(model)
                .where(model.strategy_id == strategy_id)
                .order_by(model.computed_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def insert(self, snapshot: PerformanceSnapshotBase) -> None:
        self._session.add(snapshot)
        self._session.flush()


class ShadowPerformanceSnapshotRepository(LivePerformanceSnapshotRepository):
    model = StrategyShadowPerformanceSnapshot

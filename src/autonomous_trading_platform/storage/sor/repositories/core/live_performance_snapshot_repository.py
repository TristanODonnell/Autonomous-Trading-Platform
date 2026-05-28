from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_live_performance_snapshots import (
    StrategyLivePerformanceSnapshot,
)


class LivePerformanceSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_latest(self, strategy_id: str) -> StrategyLivePerformanceSnapshot | None:
        return cast(
            StrategyLivePerformanceSnapshot | None,
            self._session.scalars(
                select(StrategyLivePerformanceSnapshot)
                .where(StrategyLivePerformanceSnapshot.strategy_id == strategy_id)
                .order_by(StrategyLivePerformanceSnapshot.computed_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def insert(self, snapshot: StrategyLivePerformanceSnapshot) -> None:
        self._session.add(snapshot)
        self._session.flush()

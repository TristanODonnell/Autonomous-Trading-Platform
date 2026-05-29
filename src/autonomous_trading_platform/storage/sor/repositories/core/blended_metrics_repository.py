from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.blended_metrics_snapshots import (
    BlendedMetricsSnapshot,
)


class BlendedMetricsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, snapshot: BlendedMetricsSnapshot) -> None:
        self._session.add(snapshot)
        self._session.flush()

    def get_latest(self, strategy_id: str) -> BlendedMetricsSnapshot | None:
        return cast(
            BlendedMetricsSnapshot | None,
            self._session.scalars(
                select(BlendedMetricsSnapshot)
                .where(BlendedMetricsSnapshot.strategy_id == strategy_id)
                .order_by(BlendedMetricsSnapshot.computed_at.desc())
                .limit(1)
            ).one_or_none(),
        )

    def list_for_strategy(
        self, strategy_id: str, *, limit: int = 50
    ) -> list[BlendedMetricsSnapshot]:
        return list(
            self._session.scalars(
                select(BlendedMetricsSnapshot)
                .where(BlendedMetricsSnapshot.strategy_id == strategy_id)
                .order_by(BlendedMetricsSnapshot.computed_at.desc())
                .limit(limit)
            ).all()
        )

    def list_for_rebalance(self, rebalance_run_id: str) -> list[BlendedMetricsSnapshot]:
        return list(
            self._session.scalars(
                select(BlendedMetricsSnapshot)
                .where(BlendedMetricsSnapshot.rebalance_run_id == rebalance_run_id)
                .order_by(BlendedMetricsSnapshot.strategy_id.asc())
            ).all()
        )

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_quality_score_history import (
    StrategyQualityScoreHistory,
)


class StrategyQualityScoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, record: StrategyQualityScoreHistory) -> None:
        self._session.add(record)
        self._session.flush()

    def get_recent_for_strategy(
        self, strategy_id: str, limit: int = 20
    ) -> list[StrategyQualityScoreHistory]:
        return list(
            self._session.scalars(
                select(StrategyQualityScoreHistory)
                .where(StrategyQualityScoreHistory.strategy_id == strategy_id)
                .order_by(StrategyQualityScoreHistory.computed_at.desc())
                .limit(limit)
            ).all()
        )

    def get_by_rebalance_run_id(self, rebalance_run_id: str) -> list[StrategyQualityScoreHistory]:
        return list(
            self._session.scalars(
                select(StrategyQualityScoreHistory).where(
                    StrategyQualityScoreHistory.rebalance_run_id == rebalance_run_id
                )
            ).all()
        )

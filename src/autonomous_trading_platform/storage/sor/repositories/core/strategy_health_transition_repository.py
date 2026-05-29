from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_health_transitions import (
    StrategyHealthTransitionRow,
)


class StrategyHealthTransitionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: StrategyHealthTransitionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_recent_for_strategy(
        self, strategy_id: str, *, limit: int = 20
    ) -> list[StrategyHealthTransitionRow]:
        return list(
            self._session.scalars(
                select(StrategyHealthTransitionRow)
                .where(StrategyHealthTransitionRow.strategy_id == strategy_id)
                .order_by(StrategyHealthTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get_all_recent(self, *, limit: int = 100) -> list[StrategyHealthTransitionRow]:
        return list(
            self._session.scalars(
                select(StrategyHealthTransitionRow)
                .order_by(StrategyHealthTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get_by_status(
        self, to_status: str, *, limit: int = 50
    ) -> list[StrategyHealthTransitionRow]:
        return list(
            self._session.scalars(
                select(StrategyHealthTransitionRow)
                .where(StrategyHealthTransitionRow.to_status == to_status)
                .order_by(StrategyHealthTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_transition import (
    DrawdownGovernanceLadderTransitionRow,
)


class DrawdownGovernanceLadderTransitionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert(self, row: DrawdownGovernanceLadderTransitionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def get_recent_for_strategy(
        self, strategy_id: str, *, limit: int = 20
    ) -> list[DrawdownGovernanceLadderTransitionRow]:
        return list(
            self._session.scalars(
                select(DrawdownGovernanceLadderTransitionRow)
                .where(DrawdownGovernanceLadderTransitionRow.strategy_id == strategy_id)
                .order_by(DrawdownGovernanceLadderTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get_all_recent(self, *, limit: int = 100) -> list[DrawdownGovernanceLadderTransitionRow]:
        return list(
            self._session.scalars(
                select(DrawdownGovernanceLadderTransitionRow)
                .order_by(DrawdownGovernanceLadderTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get_by_state(
        self, to_state: str, *, limit: int = 50
    ) -> list[DrawdownGovernanceLadderTransitionRow]:
        return list(
            self._session.scalars(
                select(DrawdownGovernanceLadderTransitionRow)
                .where(DrawdownGovernanceLadderTransitionRow.to_state == to_state)
                .order_by(DrawdownGovernanceLadderTransitionRow.created_at.desc())
                .limit(limit)
            ).all()
        )

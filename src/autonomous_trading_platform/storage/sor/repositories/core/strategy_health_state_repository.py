from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_health_state import (
    StrategyHealthStateRow,
)


class StrategyHealthStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_strategy(self, strategy_id: str) -> StrategyHealthStateRow | None:
        return cast(
            StrategyHealthStateRow | None,
            self._session.scalars(
                select(StrategyHealthStateRow).where(
                    StrategyHealthStateRow.strategy_id == strategy_id
                )
            ).one_or_none(),
        )

    def upsert(self, row: StrategyHealthStateRow) -> None:
        existing = self.get_for_strategy(row.strategy_id)
        if existing is None:
            self._session.add(row)
        else:
            existing.health_status = row.health_status
            existing.previous_health_status = row.previous_health_status
            existing.health_reason = row.health_reason
            existing.consecutive_decline_count = row.consecutive_decline_count
            existing.quality_score_trend = row.quality_score_trend
            existing.latest_quality_score = row.latest_quality_score
            existing.realized_drawdown = row.realized_drawdown
            existing.last_health_evaluated_at = row.last_health_evaluated_at
            existing.last_transition_at = row.last_transition_at
            existing.evaluation_count = (existing.evaluation_count or 0) + 1
            existing.last_rebalance_run_id = row.last_rebalance_run_id
            existing.updated_at = row.updated_at
        self._session.flush()

    def get_all(self) -> list[StrategyHealthStateRow]:
        return list(self._session.scalars(select(StrategyHealthStateRow)).all())

    def get_by_status(self, health_status: str) -> list[StrategyHealthStateRow]:
        return list(
            self._session.scalars(
                select(StrategyHealthStateRow).where(
                    StrategyHealthStateRow.health_status == health_status
                )
            ).all()
        )

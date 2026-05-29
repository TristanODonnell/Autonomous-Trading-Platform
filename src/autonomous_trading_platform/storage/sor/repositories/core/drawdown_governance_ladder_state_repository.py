from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.drawdown_governance_ladder_state import (
    DrawdownGovernanceLadderStateRow,
)


class DrawdownGovernanceLadderStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_strategy(self, strategy_id: str) -> DrawdownGovernanceLadderStateRow | None:
        return cast(
            DrawdownGovernanceLadderStateRow | None,
            self._session.scalars(
                select(DrawdownGovernanceLadderStateRow).where(
                    DrawdownGovernanceLadderStateRow.strategy_id == strategy_id
                )
            ).one_or_none(),
        )

    def upsert(self, row: DrawdownGovernanceLadderStateRow) -> None:
        existing = self.get_for_strategy(row.strategy_id)
        if existing is None:
            self._session.add(row)
        else:
            existing.ladder_state = row.ladder_state
            existing.previous_ladder_state = row.previous_ladder_state
            existing.transition_reason = row.transition_reason
            existing.realized_drawdown = row.realized_drawdown
            existing.max_drawdown_allowed = row.max_drawdown_allowed
            existing.drawdown_utilization = row.drawdown_utilization
            existing.allocation_scalar = row.allocation_scalar
            existing.cooldown_expires_at = row.cooldown_expires_at
            existing.evaluation_count = (existing.evaluation_count or 0) + 1
            existing.operator_ack_required = row.operator_ack_required
            if row.operator_acknowledged_at is not None:
                existing.operator_acknowledged_at = row.operator_acknowledged_at
                existing.operator_acknowledged_by = row.operator_acknowledged_by
            existing.last_transition_at = row.last_transition_at
            existing.last_evaluated_at = row.last_evaluated_at
            existing.updated_at = row.updated_at
        self._session.flush()

    def get_all(self) -> list[DrawdownGovernanceLadderStateRow]:
        return list(self._session.scalars(select(DrawdownGovernanceLadderStateRow)).all())

    def get_by_state(self, ladder_state: str) -> list[DrawdownGovernanceLadderStateRow]:
        return list(
            self._session.scalars(
                select(DrawdownGovernanceLadderStateRow).where(
                    DrawdownGovernanceLadderStateRow.ladder_state == ladder_state
                )
            ).all()
        )

    def get_pending_operator_ack(self) -> list[DrawdownGovernanceLadderStateRow]:
        return list(
            self._session.scalars(
                select(DrawdownGovernanceLadderStateRow).where(
                    DrawdownGovernanceLadderStateRow.operator_ack_required.is_(True)
                )
            ).all()
        )

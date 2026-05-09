from __future__ import annotations

from datetime import datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class StrategyControlStateRepository(BaseRepository):
    def get_by_strategy_id(self, strategy_id: str) -> StrategyControlState | None:
        stmt = select(StrategyControlState).where(StrategyControlState.strategy_id == strategy_id)
        return cast(StrategyControlState | None, self.session.scalars(stmt).one_or_none())

    def is_enabled(self, strategy_id: str) -> bool:
        state = self.get_by_strategy_id(strategy_id)
        if state is None:
            return True

        return bool(state.enabled)

    def set_enabled(
        self,
        *,
        strategy_id: str,
        enabled: bool,
        reason: str | None,
        updated_by: str | None,
        updated_at: datetime,
    ) -> StrategyControlState:
        existing = self.get_by_strategy_id(strategy_id)

        if existing is None:
            row = StrategyControlState(
                strategy_id=strategy_id,
                enabled=enabled,
                reason=reason,
                updated_by=updated_by,
                updated_at=updated_at,
            )
            self.session.add(row)
            return row

        existing.enabled = enabled
        existing.reason = reason
        existing.updated_by = updated_by
        existing.updated_at = updated_at
        return cast(StrategyControlState, existing)

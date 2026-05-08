from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class StrategyRuntimeStateRepository(BaseRepository):
    def get_by_strategy_id(self, strategy_id: str) -> StrategyRuntimeState | None:
        stmt = select(StrategyRuntimeState).where(StrategyRuntimeState.strategy_id == strategy_id)
        return cast(
            StrategyRuntimeState | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def upsert(self, row: StrategyRuntimeState) -> StrategyRuntimeState:
        existing = self.get_by_strategy_id(row.strategy_id)
        if existing is None:
            self.session.add(row)
            self.session.flush()
            return row

        self.session.flush()
        return existing

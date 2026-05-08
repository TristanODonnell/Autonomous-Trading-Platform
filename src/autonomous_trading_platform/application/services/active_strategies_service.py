from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.repositories.queries.active_strategies_repository import (
    ActiveStrategiesRepository,
)


class ActiveStrategiesService:
    def __init__(
        self,
        session: Session,
        repository: ActiveStrategiesRepository | None = None,
    ) -> None:
        self._repository = repository or ActiveStrategiesRepository(session)

    def list_active_strategies(self) -> list[dict[str, Any]]:
        return [
            {
                "strategy_id": row.strategy_id,
                "display_name": row.display_name,
                "strategy_type": row.strategy_type,
                "status": row.status,
                "todays_return": row.todays_return,
                "trade_count_today": row.trade_count_today,
                "allocated_capital": row.allocated_capital,
                "enabled": row.enabled,
            }
            for row in self._repository.list_active_strategies()
        ]

from __future__ import annotations

from datetime import UTC, datetime

from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyState,
)
from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)
from autonomous_trading_platform.storage.sor.repositories.strategy_runtime_state_repository import (
    StrategyRuntimeStateRepository,
)


class StrategyCheckpointWriter:
    def __init__(
        self,
        repository: StrategyRuntimeStateRepository,
        strategy_id: str,
    ) -> None:
        self.repository = repository
        self.strategy_id = strategy_id

    def mark_evaluated(self, bar_timestamp: datetime) -> None:
        row = self.repository.get_by_strategy_id(self.strategy_id)

        if row is None:
            row = StrategyRuntimeState(
                strategy_id=self.strategy_id,
                current_state=StrategyState.IDLE,
                last_evaluated_bar_timestamp=bar_timestamp,
                last_transition_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        else:
            row.last_evaluated_bar_timestamp = bar_timestamp
            row.updated_at = datetime.now(UTC)

        self.repository.upsert(row)

from __future__ import annotations

from datetime import datetime

from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    StrategyEvent,
    StrategyState,
    StrategyStateMachineService,
)
from autonomous_trading_platform.storage.sor.models.strategy_runtime_states import (
    StrategyRuntimeState,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class StrategyRuntimeStateService:
    """Coordinates persisted strategy runtime state using the strategy FSM."""

    def __init__(
        self,
        strategy_state_machine_service: StrategyStateMachineService,
    ) -> None:
        self.strategy_state_machine_service = strategy_state_machine_service

    def get_current_state(
        self,
        *,
        uow: SorUnitOfWork,
        strategy_id: str,
    ) -> StrategyState:
        row = uow.strategy_runtime_states.get_by_strategy_id(strategy_id)
        if row is None:
            return StrategyState.IDLE

        current_state: StrategyState = row.current_state
        return current_state

    def apply_event(
        self,
        *,
        uow: SorUnitOfWork,
        strategy_id: str,
        event: StrategyEvent,
        now_utc: datetime,
    ) -> StrategyState:
        current_state = self.get_current_state(
            uow=uow,
            strategy_id=strategy_id,
        )

        next_state = self.strategy_state_machine_service.apply_event(
            strategy_id=strategy_id,
            current_state=current_state,
            event=event,
        )

        existing = uow.strategy_runtime_states.get_by_strategy_id(strategy_id)
        if existing is None:
            row = StrategyRuntimeState(
                strategy_id=strategy_id,
                current_state=next_state,
                last_signal_at=now_utc if event == StrategyEvent.SIGNAL_GENERATED else None,
                last_transition_at=now_utc,
                cooldown_until=None,
                updated_at=now_utc,
            )
            uow.strategy_runtime_states.upsert(row)
            return next_state

        existing.current_state = next_state
        existing.last_transition_at = now_utc
        existing.updated_at = now_utc

        if event == StrategyEvent.SIGNAL_GENERATED:
            existing.last_signal_at = now_utc

        uow.strategy_runtime_states.upsert(existing)
        return next_state

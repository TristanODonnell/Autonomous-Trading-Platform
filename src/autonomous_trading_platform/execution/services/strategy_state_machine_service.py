from autonomous_trading_platform.contracts.common.enums import StrategyEvent, StrategyState
from autonomous_trading_platform.execution.errors import InvalidStrategyTransitionError

VALID_TRANSITIONS = {
    StrategyState.IDLE: {
        StrategyEvent.SIGNAL_GENERATED: StrategyState.SIGNALLED,
    },
    StrategyState.SIGNALLED: {
        StrategyEvent.ORDER_INTENTS_CREATED: StrategyState.PENDING,
        # Re-signal: new bar arrives before order intents were generated (e.g. holiday gap).
        StrategyEvent.SIGNAL_GENERATED: StrategyState.SIGNALLED,
        StrategyEvent.RESET: StrategyState.IDLE,
    },
    StrategyState.PENDING: {
        StrategyEvent.TARGET_POSITION_REACHED: StrategyState.IN_POSITION,
        # Re-submission: new order intents replace a previous pending batch.
        StrategyEvent.ORDER_INTENTS_CREATED: StrategyState.PENDING,
        # Re-signal from PENDING: new signal supersedes the pending intent.
        StrategyEvent.SIGNAL_GENERATED: StrategyState.SIGNALLED,
        StrategyEvent.RESET: StrategyState.IDLE,
    },
    StrategyState.IN_POSITION: {
        StrategyEvent.EXIT_SIGNAL_GENERATED: StrategyState.EXIT_PENDING,
        # Rebalance: new BUY signal while already holding a position.
        StrategyEvent.SIGNAL_GENERATED: StrategyState.SIGNALLED,
    },
    StrategyState.EXIT_PENDING: {
        StrategyEvent.POSITION_CLOSED: StrategyState.COOLDOWN,
        StrategyEvent.RESET: StrategyState.IDLE,
    },
    StrategyState.COOLDOWN: {
        StrategyEvent.COOLDOWN_COMPLETED: StrategyState.IDLE,
    },
}


class StrategyStateMachineService:
    def apply_event(
        self,
        strategy_id: str,
        current_state: StrategyState,
        event: StrategyEvent,
    ) -> StrategyState:
        next_state = VALID_TRANSITIONS.get(current_state, {}).get(event)

        if next_state is None:
            raise InvalidStrategyTransitionError(
                f"Invalid strategy transition for strategy_id={strategy_id}: "
                f"{current_state} --({event})-> ?"
            )

        return next_state

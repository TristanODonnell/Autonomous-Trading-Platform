"""
Tests for StrategyStateMachineService.

Covers:
- Every documented valid transition across all six states.
- Invalid-transition detection raises InvalidStrategyTransitionError.
- IDLE → ORDER_INTENTS_CREATED → PENDING exit-only path (Bug #4).
- Structural invariants: every state has entries; all target states are valid.
"""

from __future__ import annotations

import pytest

from autonomous_trading_platform.contracts.common.enums import StrategyEvent, StrategyState
from autonomous_trading_platform.execution.errors import InvalidStrategyTransitionError
from autonomous_trading_platform.execution.services.strategy_state_machine_service import (
    VALID_TRANSITIONS,
    StrategyStateMachineService,
)

_STRATEGY_ID = "test-strategy"


@pytest.fixture()
def fsm() -> StrategyStateMachineService:
    return StrategyStateMachineService()


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


class TestValidTransitions:
    @pytest.mark.parametrize(
        "state, event, expected",
        [
            # ── IDLE ──────────────────────────────────────────────────────
            (StrategyState.IDLE, StrategyEvent.SIGNAL_GENERATED, StrategyState.SIGNALLED),
            # Exit-only path: positions exist from a prior cycle but no new signal (Bug #4)
            (StrategyState.IDLE, StrategyEvent.ORDER_INTENTS_CREATED, StrategyState.PENDING),
            # ── SIGNALLED ─────────────────────────────────────────────────
            (StrategyState.SIGNALLED, StrategyEvent.ORDER_INTENTS_CREATED, StrategyState.PENDING),
            (StrategyState.SIGNALLED, StrategyEvent.SIGNAL_GENERATED, StrategyState.SIGNALLED),
            (StrategyState.SIGNALLED, StrategyEvent.RESET, StrategyState.IDLE),
            # ── PENDING ───────────────────────────────────────────────────
            (
                StrategyState.PENDING,
                StrategyEvent.TARGET_POSITION_REACHED,
                StrategyState.IN_POSITION,
            ),
            (StrategyState.PENDING, StrategyEvent.ORDER_INTENTS_CREATED, StrategyState.PENDING),
            (StrategyState.PENDING, StrategyEvent.SIGNAL_GENERATED, StrategyState.SIGNALLED),
            (StrategyState.PENDING, StrategyEvent.RESET, StrategyState.IDLE),
            # ── IN_POSITION ───────────────────────────────────────────────
            (
                StrategyState.IN_POSITION,
                StrategyEvent.EXIT_SIGNAL_GENERATED,
                StrategyState.EXIT_PENDING,
            ),
            (StrategyState.IN_POSITION, StrategyEvent.SIGNAL_GENERATED, StrategyState.SIGNALLED),
            # ── EXIT_PENDING ──────────────────────────────────────────────
            (StrategyState.EXIT_PENDING, StrategyEvent.POSITION_CLOSED, StrategyState.COOLDOWN),
            (StrategyState.EXIT_PENDING, StrategyEvent.RESET, StrategyState.IDLE),
            # ── COOLDOWN ──────────────────────────────────────────────────
            (StrategyState.COOLDOWN, StrategyEvent.COOLDOWN_COMPLETED, StrategyState.IDLE),
        ],
    )
    def test_valid_transition(
        self,
        fsm: StrategyStateMachineService,
        state: StrategyState,
        event: StrategyEvent,
        expected: StrategyState,
    ) -> None:
        result = fsm.apply_event(strategy_id=_STRATEGY_ID, current_state=state, event=event)
        assert result == expected


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    @pytest.mark.parametrize(
        "state, event",
        [
            # IDLE — no position/exit/cooldown events
            (StrategyState.IDLE, StrategyEvent.TARGET_POSITION_REACHED),
            (StrategyState.IDLE, StrategyEvent.EXIT_SIGNAL_GENERATED),
            (StrategyState.IDLE, StrategyEvent.POSITION_CLOSED),
            (StrategyState.IDLE, StrategyEvent.COOLDOWN_COMPLETED),
            (StrategyState.IDLE, StrategyEvent.RESET),
            # SIGNALLED — no position/exit/cooldown events
            (StrategyState.SIGNALLED, StrategyEvent.TARGET_POSITION_REACHED),
            (StrategyState.SIGNALLED, StrategyEvent.EXIT_SIGNAL_GENERATED),
            (StrategyState.SIGNALLED, StrategyEvent.POSITION_CLOSED),
            (StrategyState.SIGNALLED, StrategyEvent.COOLDOWN_COMPLETED),
            # PENDING — no exit or cooldown events
            (StrategyState.PENDING, StrategyEvent.EXIT_SIGNAL_GENERATED),
            (StrategyState.PENDING, StrategyEvent.POSITION_CLOSED),
            (StrategyState.PENDING, StrategyEvent.COOLDOWN_COMPLETED),
            # IN_POSITION — no ORDER_INTENTS_CREATED, TARGET_POSITION_REACHED, RESET, etc.
            (StrategyState.IN_POSITION, StrategyEvent.ORDER_INTENTS_CREATED),
            (StrategyState.IN_POSITION, StrategyEvent.TARGET_POSITION_REACHED),
            (StrategyState.IN_POSITION, StrategyEvent.POSITION_CLOSED),
            (StrategyState.IN_POSITION, StrategyEvent.RESET),
            (StrategyState.IN_POSITION, StrategyEvent.COOLDOWN_COMPLETED),
            # EXIT_PENDING — only POSITION_CLOSED or RESET
            (StrategyState.EXIT_PENDING, StrategyEvent.SIGNAL_GENERATED),
            (StrategyState.EXIT_PENDING, StrategyEvent.ORDER_INTENTS_CREATED),
            (StrategyState.EXIT_PENDING, StrategyEvent.TARGET_POSITION_REACHED),
            (StrategyState.EXIT_PENDING, StrategyEvent.EXIT_SIGNAL_GENERATED),
            (StrategyState.EXIT_PENDING, StrategyEvent.COOLDOWN_COMPLETED),
            # COOLDOWN — only COOLDOWN_COMPLETED
            (StrategyState.COOLDOWN, StrategyEvent.SIGNAL_GENERATED),
            (StrategyState.COOLDOWN, StrategyEvent.ORDER_INTENTS_CREATED),
            (StrategyState.COOLDOWN, StrategyEvent.TARGET_POSITION_REACHED),
            (StrategyState.COOLDOWN, StrategyEvent.EXIT_SIGNAL_GENERATED),
            (StrategyState.COOLDOWN, StrategyEvent.POSITION_CLOSED),
            (StrategyState.COOLDOWN, StrategyEvent.RESET),
        ],
    )
    def test_invalid_transition_raises(
        self,
        fsm: StrategyStateMachineService,
        state: StrategyState,
        event: StrategyEvent,
    ) -> None:
        with pytest.raises(InvalidStrategyTransitionError):
            fsm.apply_event(strategy_id=_STRATEGY_ID, current_state=state, event=event)

    def test_error_message_includes_strategy_id(self, fsm: StrategyStateMachineService) -> None:
        with pytest.raises(InvalidStrategyTransitionError) as exc_info:
            fsm.apply_event(
                strategy_id="alpha-momentum",
                current_state=StrategyState.COOLDOWN,
                event=StrategyEvent.SIGNAL_GENERATED,
            )
        assert "alpha-momentum" in str(exc_info.value)

    def test_error_message_includes_current_state(self, fsm: StrategyStateMachineService) -> None:
        with pytest.raises(InvalidStrategyTransitionError) as exc_info:
            fsm.apply_event(
                strategy_id=_STRATEGY_ID,
                current_state=StrategyState.COOLDOWN,
                event=StrategyEvent.SIGNAL_GENERATED,
            )
        assert "cooldown" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


class TestCompleteness:
    def test_every_state_has_at_least_one_valid_transition(self) -> None:
        for state in StrategyState:
            assert state in VALID_TRANSITIONS, f"{state!r} has no outgoing transitions"
            assert VALID_TRANSITIONS[state], f"{state!r} has an empty transition map"

    def test_every_transition_target_is_a_known_state(self) -> None:
        valid = set(StrategyState)
        for state, transitions in VALID_TRANSITIONS.items():
            for event, target in transitions.items():
                assert target in valid, (
                    f"{state} --({event})--> {target!r} references an unknown StrategyState"
                )

    def test_all_strategy_events_appear_in_at_least_one_transition(self) -> None:
        """Every StrategyEvent must be reachable from some state."""
        used_events: set[StrategyEvent] = set()
        for transitions in VALID_TRANSITIONS.values():
            used_events.update(transitions.keys())
        for event in StrategyEvent:
            assert event in used_events, (
                f"Event {event!r} is defined but never used in any transition"
            )


# ---------------------------------------------------------------------------
# Regression: Bug #4 — IDLE + ORDER_INTENTS_CREATED
# ---------------------------------------------------------------------------


class TestBug4ExitOnlyPath:
    """Regression for the missing IDLE → ORDER_INTENTS_CREATED transition.

    Before the fix, a cycle where positions existed from a prior bar but no
    new signal was generated would raise InvalidStrategyTransitionError when
    the exit delta was submitted as an order intent from the IDLE state.
    """

    def test_idle_order_intents_created_transitions_to_pending(
        self, fsm: StrategyStateMachineService
    ) -> None:
        result = fsm.apply_event(
            strategy_id=_STRATEGY_ID,
            current_state=StrategyState.IDLE,
            event=StrategyEvent.ORDER_INTENTS_CREATED,
        )
        assert result == StrategyState.PENDING

    def test_idle_does_not_require_signal_before_order_intents(
        self, fsm: StrategyStateMachineService
    ) -> None:
        """The path IDLE → PENDING must not require an intermediate SIGNALLED state."""
        # If this raises, the exit-only path is broken
        result = fsm.apply_event(
            strategy_id=_STRATEGY_ID,
            current_state=StrategyState.IDLE,
            event=StrategyEvent.ORDER_INTENTS_CREATED,
        )
        assert result is not None

    def test_rebalance_from_in_position_goes_to_signalled_not_exit_pending(
        self, fsm: StrategyStateMachineService
    ) -> None:
        """IN_POSITION + SIGNAL_GENERATED → SIGNALLED (rebalance, not exit)."""
        result = fsm.apply_event(
            strategy_id=_STRATEGY_ID,
            current_state=StrategyState.IN_POSITION,
            event=StrategyEvent.SIGNAL_GENERATED,
        )
        assert result == StrategyState.SIGNALLED

    def test_full_exit_lifecycle(self, fsm: StrategyStateMachineService) -> None:
        """Walk the complete exit lifecycle: IDLE → PENDING → IN_POSITION → EXIT_PENDING → COOLDOWN → IDLE."""
        state = StrategyState.IDLE
        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID, current_state=state, event=StrategyEvent.SIGNAL_GENERATED
        )
        assert state == StrategyState.SIGNALLED

        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID, current_state=state, event=StrategyEvent.ORDER_INTENTS_CREATED
        )
        assert state == StrategyState.PENDING

        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID,
            current_state=state,
            event=StrategyEvent.TARGET_POSITION_REACHED,
        )
        assert state == StrategyState.IN_POSITION

        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID, current_state=state, event=StrategyEvent.EXIT_SIGNAL_GENERATED
        )
        assert state == StrategyState.EXIT_PENDING

        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID, current_state=state, event=StrategyEvent.POSITION_CLOSED
        )
        assert state == StrategyState.COOLDOWN

        state = fsm.apply_event(
            strategy_id=_STRATEGY_ID, current_state=state, event=StrategyEvent.COOLDOWN_COMPLETED
        )
        assert state == StrategyState.IDLE

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from autonomous_trading_platform.runtime.clock import (
    MarketCalendar,
    MarketPhase,
    MarketSessionState,
)


@dataclass(frozen=True)
class SessionSafetyDecision:
    cycle_name: str
    should_run: bool
    action: str
    reason: str
    delay_seconds: int | None
    state: MarketSessionState


def evaluate_market_session_safety(
    *,
    cycle_name: str,
    now_utc: datetime,
    calendar: MarketCalendar,
    allowed_phases: Iterable[MarketPhase] = (MarketPhase.MARKET_HOURS,),
) -> SessionSafetyDecision:
    state = calendar.session_state(now_utc)
    allowed = set(allowed_phases)
    if state.current_market_phase in allowed:
        return SessionSafetyDecision(
            cycle_name=cycle_name,
            should_run=True,
            action="run",
            reason="market_session_allowed",
            delay_seconds=None,
            state=state,
        )

    delay = calendar.seconds_until_next_session_open(now_utc)
    if state.current_market_phase == MarketPhase.PRE_MARKET:
        action = "delay"
        reason = "premarket_wait_for_open"
    elif state.current_market_phase == MarketPhase.POST_MARKET:
        action = "delay"
        reason = "post_close_wait_for_next_open"
    elif state.closure_reason == "holiday":
        action = "skip"
        reason = "market_holiday"
    elif state.closure_reason == "weekend":
        action = "skip"
        reason = "weekend"
    else:
        action = "skip"
        reason = "invalid_trading_window"

    return SessionSafetyDecision(
        cycle_name=cycle_name,
        should_run=False,
        action=action,
        reason=reason,
        delay_seconds=delay,
        state=state,
    )

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from autonomous_trading_platform.cli.commands import runtime_soak_loop
from autonomous_trading_platform.runtime.clock import MarketPhase


class _OpenMarketCalendar:
    def market_phase(self, now_utc: datetime) -> MarketPhase:
        return MarketPhase.MARKET_HOURS

    def market_close(self, value: object) -> datetime:
        return datetime.now(UTC) + timedelta(hours=3)


class _Session:
    def close(self) -> None:
        pass


def test_sigterm_mid_tick_finishes_the_tick_and_starts_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deploy stops the scheduler with SIGTERM: the in-flight tick must complete and
    release its lock, and the loop must not begin another one."""
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="fast")
    runner._calendar = _OpenMarketCalendar()  # type: ignore[assignment]
    events: list[str] = []

    class _Orchestrator:
        def __init__(self, session: object) -> None:
            pass

        def run_intraday_tick(self, *, now_utc: datetime) -> SimpleNamespace:
            events.append("tick started")
            runner._sleeper.request_shutdown()  # what the SIGTERM handler does
            events.append("tick finished")
            return SimpleNamespace(correlation_id="test")

    monkeypatch.setattr(runtime_soak_loop, "get_session", lambda: _Session())
    monkeypatch.setattr(runtime_soak_loop, "PaperTradingGoldenPathOrchestrator", _Orchestrator)

    # fast mode has no sleep between ticks, so only the shutdown flag can end this loop
    exit_code = runner._run_loop(intraday_interval=0)

    assert exit_code == 0
    assert events == ["tick started", "tick finished"]
    assert runner._intraday_cycles == 1
    assert runner._lock.acquire(runtime_soak_loop._INTRADAY_LOCK_KEY) is True

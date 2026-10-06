from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from autonomous_trading_platform.cli.commands import runtime_soak_loop
from autonomous_trading_platform.runtime.clock import MarketPhase
from autonomous_trading_platform.scheduler.orchestration.eod_chain_runner import (
    ChainResult,
    ChainStatus,
)


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


class _PostMarketCalendar:
    """A trading day after the end-of-day window opened."""

    def market_phase(self, now_utc: datetime) -> MarketPhase:
        return MarketPhase.POST_MARKET

    def is_eod_eligible(self, now_utc: datetime, *, last_eod_date: object = None) -> bool:
        return last_eod_date != now_utc.astimezone(runtime_soak_loop._ET).date()

    def eod_window_open(self, d: object) -> datetime:
        return datetime.now(UTC) - timedelta(hours=1)

    def seconds_until_next_session_open(self, now_utc: datetime) -> int:
        return 3600


class _ShutdownOnSleep:
    """Stands in for InterruptibleSleeper: the first sleep ends the loop."""

    def __init__(self) -> None:
        self.is_shutdown = False
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.is_shutdown = True

    def request_shutdown(self) -> None:
        self.is_shutdown = True


def _chain_result(status: ChainStatus, failed: tuple[str, ...] = ()) -> ChainResult:
    return ChainResult(status, "parent", "corr", (), failed, (), ())


def test_failed_eod_chain_is_not_retried_on_the_next_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before the chain runner, an end-of-day failure was retried immediately and forever."""
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._calendar = _PostMarketCalendar()  # type: ignore[assignment]
    runner._sleeper = _ShutdownOnSleep()  # type: ignore[assignment]
    calls: list[datetime] = []

    class _Orchestrator:
        def __init__(self, session: object) -> None:
            pass

        def eod_chain_steps(self) -> list[object]:
            return []

        def run_eod_maintenance(self, *, now_utc: datetime, sleeper: object) -> SimpleNamespace:
            calls.append(now_utc)
            return SimpleNamespace(
                correlation_id="corr",
                chain=_chain_result(ChainStatus.FAILED, failed=("corporate_actions",)),
            )

    monkeypatch.setattr(runtime_soak_loop, "get_session", lambda: _Session())
    monkeypatch.setattr(runtime_soak_loop, "PaperTradingGoldenPathOrchestrator", _Orchestrator)

    assert runner._run_loop(intraday_interval=300) == 0

    assert len(calls) == 1
    assert runner._eod_done_for == datetime.now(UTC).astimezone(runtime_soak_loop._ET).date()
    # The loop went on to wait for the next session instead of re-running the chain.
    assert runner._sleeper.slept == [300]  # type: ignore[attr-defined]


def test_eod_runner_error_backs_off_instead_of_spinning(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._calendar = _PostMarketCalendar()  # type: ignore[assignment]
    runner._sleeper = _ShutdownOnSleep()  # type: ignore[assignment]
    calls: list[int] = []

    class _Orchestrator:
        def __init__(self, session: object) -> None:
            pass

        def eod_chain_steps(self) -> list[object]:
            return []

        def run_eod_maintenance(self, *, now_utc: datetime, sleeper: object) -> SimpleNamespace:
            calls.append(1)
            raise ConnectionError("database unreachable")

    monkeypatch.setattr(runtime_soak_loop, "get_session", lambda: _Session())
    monkeypatch.setattr(runtime_soak_loop, "PaperTradingGoldenPathOrchestrator", _Orchestrator)

    assert runner._run_loop(intraday_interval=300) == 0

    assert calls == [1]
    assert runner._eod_done_for is None  # still owed for today; retried after the backoff
    assert runner._sleeper.slept == [runtime_soak_loop._EOD_ERROR_BACKOFF_SECONDS]  # type: ignore[attr-defined]

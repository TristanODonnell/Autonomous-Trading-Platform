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


def _heartbeat_state():
    from autonomous_trading_platform.observability import metrics as m

    return m._scheduler_heartbeat_state


def _et(hour: int, minute: int = 0) -> datetime:
    from datetime import date

    return datetime.combine(
        date(2026, 10, 6), datetime.min.time(), tzinfo=runtime_soak_loop._ET
    ).replace(hour=hour, minute=minute)


def test_heartbeat_reports_market_open_and_tick_freshness() -> None:
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._last_tick_success_at = 1_700_000_000.0
    now = _et(11, 0)

    runner._publish_heartbeat(now, MarketPhase.MARKET_HOURS)

    st = _heartbeat_state()
    assert st.heartbeat_at == now.timestamp()
    assert st.market_open is True
    assert st.trading_cycle_last_success_at == 1_700_000_000.0
    assert st.eod_chain_overdue is False


def test_eod_chain_is_overdue_after_19_et_until_it_completes() -> None:
    from datetime import date

    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")

    runner._publish_heartbeat(_et(18, 30), MarketPhase.POST_MARKET)
    assert _heartbeat_state().eod_chain_overdue is False  # still inside the window

    runner._publish_heartbeat(_et(19, 5), MarketPhase.POST_MARKET)
    assert _heartbeat_state().eod_chain_overdue is True
    assert _heartbeat_state().market_open is False

    runner._eod_completed_for = date(2026, 10, 6)
    runner._publish_heartbeat(_et(19, 10), MarketPhase.POST_MARKET)
    assert _heartbeat_state().eod_chain_overdue is False


def test_eod_chain_that_failed_for_the_day_stays_overdue() -> None:
    from datetime import date

    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._eod_done_for = date(2026, 10, 6)  # the loop will not retry today...
    runner._eod_completed_for = None  # ...but nothing completed

    runner._publish_heartbeat(_et(20, 0), MarketPhase.POST_MARKET)

    assert _heartbeat_state().eod_chain_overdue is True


def test_no_eod_overdue_on_a_non_trading_day() -> None:
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._publish_heartbeat(_et(20, 0), MarketPhase.NON_TRADING_DAY)
    assert _heartbeat_state().eod_chain_overdue is False


# --- intraday ticks are aligned to the 5-minute boundary, not "300 s after the last tick"


@pytest.mark.parametrize(
    ("started", "now", "expected"),
    [
        # started 13:35:01, done 13:35:04 -> next slot 13:40:10
        (
            datetime(2026, 10, 13, 13, 35, 1, tzinfo=UTC),
            datetime(2026, 10, 13, 13, 35, 4, tzinfo=UTC),
            306,
        ),
        # a quick tick exactly on its slot
        (
            datetime(2026, 10, 13, 13, 40, 10, tzinfo=UTC),
            datetime(2026, 10, 13, 13, 40, 12, tzinfo=UTC),
            298,
        ),
        # the market-open tick at 13:30:00 (before the offset) still waits for 13:35:10
        (
            datetime(2026, 10, 13, 13, 30, 0, tzinfo=UTC),
            datetime(2026, 10, 13, 13, 30, 1, tzinfo=UTC),
            309,
        ),
        # a tick that overran its next slot skips it: 13:45:10 is gone, wait for 13:50:10
        (
            datetime(2026, 10, 13, 13, 40, 10, tzinfo=UTC),
            datetime(2026, 10, 13, 13, 46, 0, tzinfo=UTC),
            250,
        ),
        # fractional seconds round up so we never wake before the slot
        (
            datetime(2026, 10, 13, 13, 35, 10, tzinfo=UTC),
            datetime(2026, 10, 13, 13, 40, 7, 500_000, tzinfo=UTC),
            3,
        ),
    ],
)
def test_seconds_until_next_tick_targets_next_window_plus_offset(
    started: datetime, now: datetime, expected: int
) -> None:
    assert runtime_soak_loop._INTRADAY_TICK_OFFSET_SECONDS == 10
    assert runtime_soak_loop._seconds_until_next_tick(started, now, 300) == expected


def test_tick_offset_stays_inside_the_trading_cycle_ingestion_grace() -> None:
    """The trading cycle runs in safe mode (no evaluation) when it starts more than the
    ingestion grace after the 5-minute boundary, so the tick slot must sit inside it."""
    from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
        build_trading_cycle_window,
    )

    slot = datetime(2026, 10, 13, 13, 40, 0, tzinfo=UTC) + timedelta(
        seconds=runtime_soak_loop._INTRADAY_TICK_OFFSET_SECONDS
    )
    # allow a few seconds for ingestion + features before the trading cycle looks at the clock
    window = build_trading_cycle_window(now_utc=slot + timedelta(seconds=20))
    assert window.cycle_end == datetime(2026, 10, 13, 13, 40, 0, tzinfo=UTC)
    assert slot + timedelta(seconds=20) <= window.ingestion_deadline


def test_loop_sleeps_until_the_next_slot_not_a_flat_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Box A, 2026-10-12..14: a flat 300 s sleep after each tick drifted ~3 s per tick,
    crossed the 60 s grace around 11:00 ET and every later tick skipped trading."""
    runner = runtime_soak_loop._PaperTradingSoakRunner(mode="realistic")
    runner._calendar = _OpenMarketCalendar()  # type: ignore[assignment]
    tick_done_at = datetime(2026, 10, 13, 13, 35, 4, tzinfo=UTC)

    class _Clock:
        def now(self) -> datetime:
            return tick_done_at

    class _Sleeper:
        def __init__(self) -> None:
            self.is_shutdown = False
            self.slept: list[int] = []

        def sleep(self, seconds: int) -> None:
            self.slept.append(seconds)
            self.is_shutdown = True

    class _Orchestrator:
        def __init__(self, session: object) -> None:
            pass

        def run_intraday_tick(self, *, now_utc: datetime) -> SimpleNamespace:
            return SimpleNamespace(correlation_id="test")

    runner._clock = _Clock()  # type: ignore[assignment]
    sleeper = _Sleeper()
    runner._sleeper = sleeper  # type: ignore[assignment]
    monkeypatch.setattr(runtime_soak_loop, "get_session", lambda: _Session())
    monkeypatch.setattr(runtime_soak_loop, "PaperTradingGoldenPathOrchestrator", _Orchestrator)

    runner._run_loop(intraday_interval=300)

    assert sleeper.slept == [306]  # 13:35:04 -> 13:40:10, not 13:40:04

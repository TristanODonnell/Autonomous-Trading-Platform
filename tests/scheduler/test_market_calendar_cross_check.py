from __future__ import annotations

from datetime import UTC, date, datetime, time

from autonomous_trading_platform.runtime.clock import RealMarketCalendar
from autonomous_trading_platform.scheduler.services.market_calendar_cross_check import (
    cross_check_market_calendar,
    run_calendar_cross_check,
)

calendar = RealMarketCalendar()
OPEN, CLOSE, HALF = time(9, 30), time(16, 0), time(13, 0)


def test_agreeing_calendars_have_no_mismatches() -> None:
    start, end = date(2026, 10, 5), date(2026, 10, 16)
    broker = {d: (OPEN, CLOSE) for d in calendar.trading_days(start, end)}
    assert cross_check_market_calendar(calendar, broker, start=start, end=end) == []


def test_half_day_is_compared_on_close_time() -> None:
    # 27 Nov 2026 (day after Thanksgiving) closes at 13:00 locally; broker says 16:00
    day = date(2026, 11, 27)
    broker = {day: (OPEN, CLOSE)}
    [m] = cross_check_market_calendar(calendar, broker, start=day, end=day)
    assert (m.kind, m.local, m.broker) == ("close_time", "13:00:00", "16:00:00")
    assert cross_check_market_calendar(calendar, {day: (OPEN, HALF)}, start=day, end=day) == []


def test_session_missing_on_either_side_is_reported() -> None:
    thanksgiving, friday = date(2026, 11, 26), date(2026, 11, 27)
    # broker thinks Thanksgiving is a session; broker has no Friday session
    broker = {thanksgiving: (OPEN, CLOSE)}
    mismatches = cross_check_market_calendar(calendar, broker, start=thanksgiving, end=friday)
    assert [(m.day, m.kind) for m in mismatches] == [
        (thanksgiving, "session_only_broker"),
        (friday, "session_only_local"),
    ]


def test_run_summary_covers_the_next_two_weeks_from_the_et_date() -> None:
    now = datetime(2026, 10, 5, 23, 30, tzinfo=UTC)  # 19:30 ET on the 5th
    broker = {
        d: (OPEN, CLOSE) for d in calendar.trading_days(date(2026, 10, 6), date(2026, 10, 19))
    }
    summary = run_calendar_cross_check(calendar, now_utc=now, broker_sessions=broker)
    assert summary["checked_from"] == "2026-10-06"
    assert summary["checked_to"] == "2026-10-19"
    assert summary["broker_sessions"] == 10
    assert summary["mismatches"] == []

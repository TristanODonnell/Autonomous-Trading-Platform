from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from autonomous_trading_platform.runtime.clock import HistoricalMarketCalendar
from autonomous_trading_platform.scheduler.session_safety import evaluate_market_session_safety

_ET = ZoneInfo("America/New_York")


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_ET).astimezone(UTC)


def test_session_safety_allows_normal_trading_day_market_hours() -> None:
    day = date(2024, 1, 8)
    decision = evaluate_market_session_safety(
        cycle_name="trading_cycle",
        now_utc=_et(day, 12, 0),
        calendar=HistoricalMarketCalendar(),
    )

    assert decision.should_run is True
    assert decision.action == "run"
    assert decision.reason == "market_session_allowed"


def test_session_safety_delays_premarket_until_open() -> None:
    day = date(2024, 1, 8)
    decision = evaluate_market_session_safety(
        cycle_name="trading_cycle",
        now_utc=_et(day, 8, 0),
        calendar=HistoricalMarketCalendar(),
    )

    assert decision.should_run is False
    assert decision.action == "delay"
    assert decision.reason == "premarket_wait_for_open"
    assert decision.delay_seconds == 5400


def test_session_safety_skips_weekend() -> None:
    decision = evaluate_market_session_safety(
        cycle_name="trading_cycle",
        now_utc=_et(date(2024, 1, 6), 12, 0),
        calendar=HistoricalMarketCalendar(),
    )

    assert decision.should_run is False
    assert decision.action == "skip"
    assert decision.reason == "weekend"


def test_session_safety_skips_configured_holiday() -> None:
    class HolidayCalendar(HistoricalMarketCalendar):
        def is_trading_day(self, value: date) -> bool:
            return value != date(2024, 1, 8) and super().is_trading_day(value)

    decision = evaluate_market_session_safety(
        cycle_name="trading_cycle",
        now_utc=_et(date(2024, 1, 8), 12, 0),
        calendar=HolidayCalendar(),
    )

    assert decision.should_run is False
    assert decision.action == "skip"
    assert decision.reason == "market_holiday"


def test_session_safety_delays_post_close_and_early_close_sessions() -> None:
    early_close_day = date(2024, 1, 8)
    decision = evaluate_market_session_safety(
        cycle_name="trading_cycle",
        now_utc=_et(early_close_day, 14, 0),
        calendar=HistoricalMarketCalendar(early_closes={early_close_day: time(13, 0)}),
    )

    assert decision.should_run is False
    assert decision.action == "delay"
    assert decision.reason == "post_close_wait_for_next_open"
    assert decision.state.is_early_close is True

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from autonomous_trading_platform.application.services.health.market_session_health_service import (
    MarketSessionHealthService,
)
from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthStatus,
)
from autonomous_trading_platform.runtime.clock import (
    FakeTradingClock,
    HistoricalMarketCalendar,
    RealMarketCalendar,
)

_ET = ZoneInfo("America/New_York")
_TRADING_DAY = date(2024, 1, 8)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_ET).astimezone(UTC)


def test_market_session_health_exposes_runtime_session_state() -> None:
    service = MarketSessionHealthService(
        clock=FakeTradingClock(_et(_TRADING_DAY, 12, 0)),
        calendar=HistoricalMarketCalendar(),
    )

    report = service.check()
    check = report.checks[0]

    assert report.service == "market_session"
    assert report.status == HealthStatus.OK
    assert check.check_name == HealthCheckName.MARKET_SESSION_STATE
    assert check.metadata["current_market_phase"] == "MARKET_HOURS"
    assert check.metadata["current_trading_date"] == "2024-01-08"
    assert check.metadata["next_market_open"] == _et(date(2024, 1, 9), 9, 30).isoformat()
    assert check.metadata["next_market_close"] == _et(_TRADING_DAY, 16, 0).isoformat()
    assert check.metadata["eod_eligible"] is False
    assert check.metadata["calendar_source"] == "historical_weekday"


def test_market_session_health_logs_eod_and_early_close(caplog) -> None:
    caplog.set_level(logging.INFO)
    service = MarketSessionHealthService(
        clock=FakeTradingClock(_et(_TRADING_DAY, 18, 1)),
        calendar=HistoricalMarketCalendar(early_closes={_TRADING_DAY: time(13, 0)}),
    )

    service.check()

    messages = [record.getMessage() for record in caplog.records]
    assert "market_session.close" in messages
    assert "market_session.early_close" in messages
    assert "market_session.eod_window_open" in messages


def test_market_session_health_logs_holiday_closure(caplog) -> None:
    caplog.set_level(logging.INFO)
    holiday = date(2024, 12, 25)
    service = MarketSessionHealthService(
        clock=FakeTradingClock(_et(holiday, 12, 0)),
        calendar=RealMarketCalendar(),
    )

    service.check()

    messages = [record.getMessage() for record in caplog.records]
    assert "market_session.holiday_closure" in messages

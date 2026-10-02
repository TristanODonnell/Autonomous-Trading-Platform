"""Backtest session hours follow the exchange clock (step 5c-D, F5).

Before the fix the backtest ingested and ticked 14:30-21:00 UTC all year, so from the
switch to daylight saving every day lost its first trading hour (66 bars, not 78) and
kept ticking an hour past the close.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from autonomous_trading_platform.application.services import platform_backtest_service
from autonomous_trading_platform.application.services.platform_replay import ingestion_hooks
from autonomous_trading_platform.contracts.runtime.platform_replay import PlatformReplayContext
from autonomous_trading_platform.universe.services.market_calendar_service import (
    MarketCalendarService,
)

_WINTER = date(2024, 1, 10)  # EST, UTC-5
_SUMMER = date(2024, 7, 10)  # EDT, UTC-4
_EARLY_CLOSE_WINTER = date(2024, 11, 29)  # day after Thanksgiving, 13:00 ET
_EARLY_CLOSE_SUMMER = date(2024, 7, 3)  # eve of Independence Day, 13:00 ET


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "open_utc", "close_utc"),
    [
        (_WINTER, _utc(_WINTER, 14, 30), _utc(_WINTER, 21)),
        (_SUMMER, _utc(_SUMMER, 13, 30), _utc(_SUMMER, 20)),
        (_EARLY_CLOSE_WINTER, _utc(_EARLY_CLOSE_WINTER, 14, 30), _utc(_EARLY_CLOSE_WINTER, 18)),
        (_EARLY_CLOSE_SUMMER, _utc(_EARLY_CLOSE_SUMMER, 13, 30), _utc(_EARLY_CLOSE_SUMMER, 17)),
    ],
)
def test_regular_session_in_utc(day, open_utc, close_utc) -> None:
    assert MarketCalendarService().regular_session_utc(day) == (open_utc, close_utc)


@pytest.mark.parametrize("day", [_WINTER, _SUMMER])
def test_full_day_has_78_five_minute_bars_from_the_open(day) -> None:
    open_utc, close_utc = MarketCalendarService().regular_session_utc(day)
    bars = platform_backtest_service._intraday_bar_timestamps(day, 5)

    assert len(bars) == 78
    assert bars[0] == open_utc
    assert bars[-1] == close_utc - timedelta(minutes=5)


def test_early_close_day_stops_at_one_pm_eastern() -> None:
    bars = platform_backtest_service._intraday_bar_timestamps(_EARLY_CLOSE_SUMMER, 5)

    assert len(bars) == 42  # 09:30-13:00 ET
    assert bars[-1] == _utc(_EARLY_CLOSE_SUMMER, 16, 55)


def test_daily_cadence_ticks_once_at_the_close() -> None:
    assert platform_backtest_service._intraday_bar_timestamps(_SUMMER, 390) == [_utc(_SUMMER, 20)]
    assert platform_backtest_service._market_close_ts(_WINTER) == _utc(_WINTER, 21)


@pytest.mark.parametrize("day", [_WINTER, _SUMMER, _EARLY_CLOSE_SUMMER])
def test_full_day_ingestion_requests_the_exchange_session(day, db_session, monkeypatch) -> None:
    captured: dict = {}

    def fake_ingestion_cycle(**kwargs):
        captured.update(kwargs)
        return {"dataset_version_id": "raw_bars_test", "row_count": 0}

    monkeypatch.setattr(ingestion_hooks, "run_market_ingestion_cycle", fake_ingestion_cycle)
    close = platform_backtest_service._market_close_ts(day)
    context = PlatformReplayContext.create(symbols=["AAA"], timestamp=close)

    ingestion_hooks.run_ingestion_at_timestamp(
        session=db_session, timestamp=close, replay_context=context, full_day=True
    )

    expected = MarketCalendarService().regular_session_utc(day)
    assert (captured["cycle_start_override"], captured["cycle_end_override"]) == expected

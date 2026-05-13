from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from autonomous_trading_platform.runtime.clock import (
    HistoricalMarketCalendar,
    MarketPhase,
)

_ET = ZoneInfo("America/New_York")

# Monday 2024-01-08 — a confirmed trading day
_TRADING_DAY = date(2024, 1, 8)
# Saturday 2024-01-06
_WEEKEND_DAY = date(2024, 1, 6)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_ET).astimezone(UTC)


class TestMarketPhase:
    def _cal(self) -> HistoricalMarketCalendar:
        return HistoricalMarketCalendar()

    def test_market_hours_during_session(self) -> None:
        cal = self._cal()
        assert cal.market_phase(_et(_TRADING_DAY, 12, 0)) == MarketPhase.MARKET_HOURS

    def test_pre_market_before_open(self) -> None:
        cal = self._cal()
        assert cal.market_phase(_et(_TRADING_DAY, 8, 0)) == MarketPhase.PRE_MARKET

    def test_post_market_after_close(self) -> None:
        cal = self._cal()
        assert cal.market_phase(_et(_TRADING_DAY, 17, 0)) == MarketPhase.POST_MARKET

    def test_non_trading_day_on_weekend(self) -> None:
        cal = self._cal()
        assert cal.market_phase(_et(_WEEKEND_DAY, 12, 0)) == MarketPhase.NON_TRADING_DAY


class TestIsEodEligible:
    def _cal(self) -> HistoricalMarketCalendar:
        return HistoricalMarketCalendar()

    def test_eligible_in_post_market_after_eod_window(self) -> None:
        cal = self._cal()
        # 18:01 ET — past the 18:00 EOD window
        now = _et(_TRADING_DAY, 18, 1)
        assert cal.is_eod_eligible(now, last_eod_date=None) is True

    def test_not_eligible_before_eod_window_opens(self) -> None:
        cal = self._cal()
        # 17:00 ET — after market close but before EOD window
        now = _et(_TRADING_DAY, 17, 0)
        assert cal.is_eod_eligible(now, last_eod_date=None) is False

    def test_not_eligible_during_market_hours(self) -> None:
        cal = self._cal()
        now = _et(_TRADING_DAY, 12, 0)
        assert cal.is_eod_eligible(now, last_eod_date=None) is False

    def test_not_eligible_on_non_trading_day(self) -> None:
        cal = self._cal()
        # Sunday 18:30 ET — past "window" time but not a trading day
        weekend = date(2024, 1, 7)  # Sunday
        now = _et(weekend, 18, 30)
        assert cal.is_eod_eligible(now, last_eod_date=None) is False

    def test_eod_deduplication_same_day_is_not_eligible_again(self) -> None:
        """Once EOD has run for today, is_eod_eligible must return False — prevents double-run."""
        cal = self._cal()
        now = _et(_TRADING_DAY, 18, 1)
        assert cal.is_eod_eligible(now, last_eod_date=_TRADING_DAY) is False

    def test_eod_eligible_after_previous_day_eod(self) -> None:
        """EOD from yesterday does not block today's eligibility."""
        cal = self._cal()
        # Tuesday 2024-01-09, past EOD window
        tuesday = date(2024, 1, 9)
        monday = date(2024, 1, 8)
        now = _et(tuesday, 18, 1)
        assert cal.is_eod_eligible(now, last_eod_date=monday) is True


class TestSecondsUntilNextSessionOpen:
    def _cal(self) -> HistoricalMarketCalendar:
        return HistoricalMarketCalendar()

    def test_skips_weekend_to_monday(self) -> None:
        cal = self._cal()
        # Friday 16:01 ET — past close; next open is Monday 09:30 ET
        friday = date(2024, 1, 5)
        now = _et(friday, 16, 1)
        seconds = cal.seconds_until_next_session_open(now)
        # Should be roughly 64.5 hours (Friday 16:01 → Monday 09:30)
        expected_low = int(timedelta(hours=64).total_seconds())
        expected_high = int(timedelta(hours=66).total_seconds())
        assert expected_low <= seconds <= expected_high

    def test_returns_positive_value_from_trading_day_after_close(self) -> None:
        cal = self._cal()
        now = _et(_TRADING_DAY, 18, 0)
        seconds = cal.seconds_until_next_session_open(now)
        assert seconds > 0

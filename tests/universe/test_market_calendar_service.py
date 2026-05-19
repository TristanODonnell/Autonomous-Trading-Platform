from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from autonomous_trading_platform.universe.services.market_calendar_service import (
    MarketCalendarService,
)


@pytest.fixture
def service() -> MarketCalendarService:
    return MarketCalendarService()


class TestIsTradingDay:
    def test_weekday_is_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 1, 15)) is True  # Wednesday

    def test_saturday_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 1, 18)) is False

    def test_sunday_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 1, 19)) is False

    def test_new_years_day_2025_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 1, 1)) is False

    def test_mlk_day_2025_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 1, 20)) is False

    def test_christmas_2025_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 12, 25)) is False

    def test_thanksgiving_2025_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 11, 27)) is False

    def test_day_after_thanksgiving_2025_is_trading_day(
        self, service: MarketCalendarService
    ) -> None:
        assert service.is_trading_day(date(2025, 11, 28)) is True

    def test_good_friday_2025_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2025, 4, 18)) is False

    def test_new_years_2026_is_not_trading_day(self, service: MarketCalendarService) -> None:
        assert service.is_trading_day(date(2026, 1, 1)) is False


class TestIsEarlyClose:
    def test_black_friday_2025_is_early_close(self, service: MarketCalendarService) -> None:
        assert service.is_early_close(date(2025, 11, 28)) is True

    def test_christmas_eve_2025_is_early_close(self, service: MarketCalendarService) -> None:
        assert service.is_early_close(date(2025, 12, 24)) is True

    def test_july_3_2025_is_early_close(self, service: MarketCalendarService) -> None:
        assert service.is_early_close(date(2025, 7, 3)) is True

    def test_regular_trading_day_is_not_early_close(self, service: MarketCalendarService) -> None:
        assert service.is_early_close(date(2025, 1, 15)) is False


class TestIsHoliday:
    def test_new_years_is_holiday(self, service: MarketCalendarService) -> None:
        assert service.is_holiday(date(2025, 1, 1)) is True

    def test_regular_weekday_is_not_holiday(self, service: MarketCalendarService) -> None:
        assert service.is_holiday(date(2025, 1, 15)) is False

    def test_weekend_is_not_a_holiday(self, service: MarketCalendarService) -> None:
        assert service.is_holiday(date(2025, 1, 18)) is False  # Saturday


class TestGetTradingDays:
    def test_returns_weekdays_only(self, service: MarketCalendarService) -> None:
        days = service.get_trading_days(date(2025, 1, 13), date(2025, 1, 17))
        assert days == [
            date(2025, 1, 13),
            date(2025, 1, 14),
            date(2025, 1, 15),
            date(2025, 1, 16),
            date(2025, 1, 17),
        ]

    def test_excludes_holiday(self, service: MarketCalendarService) -> None:
        days = service.get_trading_days(date(2025, 1, 17), date(2025, 1, 21))
        # Jan 20 = MLK Day (holiday), Jan 18-19 = weekend
        assert date(2025, 1, 20) not in days
        assert date(2025, 1, 17) in days
        assert date(2025, 1, 21) in days

    def test_empty_range(self, service: MarketCalendarService) -> None:
        days = service.get_trading_days(date(2025, 1, 18), date(2025, 1, 19))
        assert days == []

    def test_single_trading_day(self, service: MarketCalendarService) -> None:
        days = service.get_trading_days(date(2025, 1, 15), date(2025, 1, 15))
        assert days == [date(2025, 1, 15)]


class TestIsMarketOpenNow:
    def test_open_during_regular_hours(self, service: MarketCalendarService) -> None:
        # Wednesday Jan 15 2025, 15:00 UTC = 10 AM ET — regular hours
        as_of = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        assert service.is_market_open_now(as_of) is True

    def test_closed_before_market_open(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 15, 13, 0, tzinfo=UTC)
        assert service.is_market_open_now(as_of) is False

    def test_closed_after_market_close(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 15, 22, 0, tzinfo=UTC)
        assert service.is_market_open_now(as_of) is False

    def test_closed_on_weekend(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 18, 15, 0, tzinfo=UTC)
        assert service.is_market_open_now(as_of) is False

    def test_closed_on_holiday(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 1, 15, 0, tzinfo=UTC)
        assert service.is_market_open_now(as_of) is False


class TestShouldRefreshToday:
    def test_daily_cadence_on_trading_day(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        assert service.should_refresh_today("daily", as_of) is True

    def test_daily_cadence_on_holiday(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 1, 15, 0, tzinfo=UTC)
        assert service.should_refresh_today("daily", as_of) is False

    def test_weekly_cadence_on_monday_trading_day(self, service: MarketCalendarService) -> None:
        # Jan 13 2025 = Monday, is a trading day
        as_of = datetime(2025, 1, 13, 15, 0, tzinfo=UTC)
        assert service.should_refresh_today("weekly", as_of) is True

    def test_weekly_cadence_on_non_monday(self, service: MarketCalendarService) -> None:
        # Jan 15 2025 = Wednesday
        as_of = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        assert service.should_refresh_today("weekly", as_of) is False

    def test_unsupported_cadence_raises(self, service: MarketCalendarService) -> None:
        as_of = datetime(2025, 1, 15, 15, 0, tzinfo=UTC)
        with pytest.raises(ValueError, match="Unsupported cadence"):
            service.should_refresh_today("hourly", as_of)


class TestGetNextTradingDay:
    def test_next_trading_day_from_friday(self, service: MarketCalendarService) -> None:
        next_day = service.get_next_trading_day(date(2025, 1, 17))
        assert next_day == date(2025, 1, 21)

    def test_next_trading_day_skips_holiday(self, service: MarketCalendarService) -> None:
        # Dec 24 = early close, Dec 25 = holiday, Dec 26 = next trading day (Friday)
        next_day = service.get_next_trading_day(date(2025, 12, 24))
        assert next_day == date(2025, 12, 26)

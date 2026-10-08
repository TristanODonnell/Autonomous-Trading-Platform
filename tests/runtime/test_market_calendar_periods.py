from __future__ import annotations

from datetime import date

import pytest

from autonomous_trading_platform.runtime.clock import RealMarketCalendar

calendar = RealMarketCalendar()


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 5), True),  # an ordinary Monday
        (date(2026, 10, 6), False),  # Tuesday after a traded Monday
        (date(2026, 9, 7), False),  # Labor Day: holiday, not a session
        (date(2026, 9, 8), True),  # Tuesday after Labor Day is the week's first session
        (date(2026, 10, 10), False),  # Saturday
    ],
)
def test_first_trading_day_of_week(day: date, expected: bool) -> None:
    assert calendar.is_first_trading_day_of_week(day) is expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 30), True),  # Friday; the 31st is a Saturday
        (date(2026, 10, 29), False),
        (date(2026, 10, 31), False),  # weekend, not a session
        (date(2026, 12, 31), True),  # Thursday, last session of the year
        (date(2026, 11, 30), True),  # Monday
        (date(2026, 2, 27), True),  # Friday; 28 Feb 2026 is a Saturday
    ],
)
def test_last_trading_day_of_month(day: date, expected: bool) -> None:
    assert calendar.is_last_trading_day_of_month(day) is expected

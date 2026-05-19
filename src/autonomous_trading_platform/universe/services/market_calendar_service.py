from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

# Known NYSE holidays for 2025-2026.
_NYSE_HOLIDAYS: frozenset[date] = frozenset(
    [
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ]
)

# NYSE early-close days (market closes at 1 PM ET instead of 4 PM ET).
_NYSE_EARLY_CLOSES: frozenset[date] = frozenset(
    [
        date(2025, 7, 3),
        date(2025, 11, 28),
        date(2025, 12, 24),
        date(2026, 11, 27),
        date(2026, 12, 24),
    ]
)

# UTC equivalents for regular NYSE session (approximations; does not adjust for DST).
_MARKET_OPEN_UTC = time(14, 30)
_MARKET_CLOSE_UTC = time(21, 0)
_EARLY_CLOSE_UTC = time(18, 0)


class MarketCalendarProvider(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def is_early_close(self, d: date) -> bool: ...
    def get_trading_days(self, start: date, end: date) -> list[date]: ...


class StaticNYSECalendarProvider:
    def is_trading_day(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        return d not in _NYSE_HOLIDAYS

    def is_early_close(self, d: date) -> bool:
        return d in _NYSE_EARLY_CLOSES

    def get_trading_days(self, start: date, end: date) -> list[date]:
        result: list[date] = []
        current = start
        while current <= end:
            if self.is_trading_day(current):
                result.append(current)
            current += timedelta(days=1)
        return result


class MarketCalendarService:
    def __init__(self, provider: MarketCalendarProvider | None = None) -> None:
        self._provider: MarketCalendarProvider = provider or StaticNYSECalendarProvider()

    def is_trading_day(self, d: date) -> bool:
        return self._provider.is_trading_day(d)

    def is_early_close(self, d: date) -> bool:
        return self._provider.is_early_close(d)

    def is_holiday(self, d: date) -> bool:
        if d.weekday() >= 5:
            return False
        return not self._provider.is_trading_day(d)

    def get_trading_days(self, start: date, end: date) -> list[date]:
        return self._provider.get_trading_days(start, end)

    def is_market_open_now(self, as_of: datetime | None = None) -> bool:
        now = _ensure_utc(as_of or datetime.now(UTC))
        d = now.date()
        if not self.is_trading_day(d):
            return False
        t = now.timetz().replace(tzinfo=None)
        close = _EARLY_CLOSE_UTC if self.is_early_close(d) else _MARKET_CLOSE_UTC
        return _MARKET_OPEN_UTC <= t <= close

    def get_next_trading_day(self, from_date: date) -> date:
        candidate = from_date + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def should_refresh_today(self, cadence: str, as_of: datetime | None = None) -> bool:
        now = _ensure_utc(as_of or datetime.now(UTC))
        d = now.date()
        if not self.is_trading_day(d):
            return False
        if cadence == "daily":
            return True
        if cadence == "weekly":
            return d.weekday() == 0
        raise ValueError(f"Unsupported cadence: {cadence!r}")


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

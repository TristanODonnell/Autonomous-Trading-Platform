from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class TradingClock:
    def now(self) -> datetime:
        raise NotImplementedError

    def set_time(self, value: datetime) -> None:
        raise NotImplementedError

    def advance_to(self, value: datetime) -> None:
        raise NotImplementedError


class FakeTradingClock(TradingClock):
    def __init__(self, initial_time: datetime) -> None:
        self._now = _ensure_utc(initial_time)

    def now(self) -> datetime:
        return self._now

    def set_time(self, value: datetime) -> None:
        self._now = _ensure_utc(value)

    def advance_to(self, value: datetime) -> None:
        self.set_time(value)


class HistoricalTradingClock(FakeTradingClock):
    pass


class RealTradingClock(TradingClock):
    def now(self) -> datetime:
        return datetime.now(UTC)

    def set_time(self, value: datetime) -> None:
        raise RuntimeError("RealTradingClock cannot be set")

    def advance_to(self, value: datetime) -> None:
        raise RuntimeError("RealTradingClock cannot be advanced")


class MarketCalendar:
    def is_trading_day(self, value: date) -> bool:
        raise NotImplementedError

    def market_open(self, value: date) -> datetime:
        raise NotImplementedError

    def market_close(self, value: date) -> datetime:
        raise NotImplementedError

    def session_times(self, value: date) -> tuple[datetime, datetime]:
        return self.market_open(value), self.market_close(value)

    def scheduled_times(
        self,
        start: date,
        end: date,
        interval: timedelta,
        *,
        max_ticks: int | None = None,
    ) -> Iterator[datetime]:
        raise NotImplementedError


class HistoricalMarketCalendar(MarketCalendar):
    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5

    def market_open(self, value: date) -> datetime:
        return datetime.combine(value, time(9, 30), tzinfo=_ET).astimezone(UTC)

    def market_close(self, value: date) -> datetime:
        return datetime.combine(value, time(16, 0), tzinfo=_ET).astimezone(UTC)

    def scheduled_times(
        self,
        start: date,
        end: date,
        interval: timedelta,
        *,
        max_ticks: int | None = None,
    ) -> Iterator[datetime]:
        current = start
        emitted = 0
        while current <= end:
            if self.is_trading_day(current):
                session_start, session_end = self.session_times(current)
                tick = session_start
                while tick <= session_end:
                    if max_ticks is not None and emitted >= max_ticks:
                        return
                    yield tick
                    emitted += 1
                    tick += interval
            current += timedelta(days=1)


class FakeMarketCalendar(HistoricalMarketCalendar):
    pass


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from functools import lru_cache
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _nyse_calendar():
    import exchange_calendars as ec  # lazy import — only needed by RealMarketCalendar

    return ec.get_calendar("XNYS")


class MarketPhase(Enum):
    PRE_MARKET = "PRE_MARKET"
    MARKET_HOURS = "MARKET_HOURS"
    POST_MARKET = "POST_MARKET"
    # Non-trading day (weekend or holiday).  Expand to HOLIDAY when the NYSE
    # holiday calendar is added; callers that only check != MARKET_HOURS are
    # already forward-compatible.
    NON_TRADING_DAY = "NON_TRADING_DAY"


@dataclass(frozen=True)
class MarketSessionState:
    current_market_phase: MarketPhase
    next_market_open: datetime | None
    next_market_close: datetime | None
    current_trading_date: date | None
    eod_eligible: bool
    calendar_source: str
    is_trading_day: bool
    is_early_close: bool
    closure_reason: str | None = None


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
    @property
    def calendar_source(self) -> str:
        return type(self).__name__

    def is_trading_day(self, value: date) -> bool:
        raise NotImplementedError

    def market_open(self, value: date) -> datetime:
        raise NotImplementedError

    def market_close(self, value: date) -> datetime:
        raise NotImplementedError

    def session_times(self, value: date) -> tuple[datetime, datetime]:
        return self.market_open(value), self.market_close(value)

    def trading_days(self, start: date, end: date) -> Iterator[date]:
        current = start
        while current <= end:
            if self.is_trading_day(current):
                yield current
            current += timedelta(days=1)

    def scheduled_times(
        self,
        start: date,
        end: date,
        interval: timedelta,
        *,
        max_ticks: int | None = None,
    ) -> Iterator[datetime]:
        raise NotImplementedError

    def market_phase(self, now_utc: datetime) -> MarketPhase:
        raise NotImplementedError

    def current_trading_date(self, now_utc: datetime) -> date | None:
        candidate = _ensure_utc(now_utc).astimezone(_ET).date()
        if self.is_trading_day(candidate):
            return candidate
        return None

    def next_session_open(self, now_utc: datetime) -> datetime | None:
        now_utc = _ensure_utc(now_utc)
        candidate = now_utc.astimezone(_ET).date()
        for _ in range(14):
            if self.is_trading_day(candidate):
                open_ = self.market_open(candidate)
                if now_utc < open_:
                    return open_
            candidate += timedelta(days=1)
        return None

    def next_session_close(self, now_utc: datetime) -> datetime | None:
        now_utc = _ensure_utc(now_utc)
        candidate = now_utc.astimezone(_ET).date()
        for _ in range(14):
            if self.is_trading_day(candidate):
                close = self.market_close(candidate)
                if now_utc < close:
                    return close
            candidate += timedelta(days=1)
        return None

    def is_early_close(self, value: date) -> bool:
        if not self.is_trading_day(value):
            return False
        return self.market_close(value).astimezone(_ET).time() < time(16, 0)

    def closure_reason(self, value: date) -> str | None:
        if self.is_trading_day(value):
            return None
        if value.weekday() >= 5:
            return "weekend"
        return "holiday"

    def session_state(
        self,
        now_utc: datetime,
        *,
        last_eod_date: date | None = None,
        eod_hour: int = 18,
        eod_minute: int = 0,
    ) -> MarketSessionState:
        now_utc = _ensure_utc(now_utc)
        local_date = now_utc.astimezone(_ET).date()
        trading_date = self.current_trading_date(now_utc)
        return MarketSessionState(
            current_market_phase=self.market_phase(now_utc),
            next_market_open=self.next_session_open(now_utc),
            next_market_close=self.next_session_close(now_utc),
            current_trading_date=trading_date,
            eod_eligible=self.is_eod_eligible(
                now_utc,
                eod_hour=eod_hour,
                eod_minute=eod_minute,
                last_eod_date=last_eod_date,
            ),
            calendar_source=self.calendar_source,
            is_trading_day=trading_date is not None,
            is_early_close=self.is_early_close(local_date),
            closure_reason=self.closure_reason(local_date),
        )

    def seconds_until_next_session_open(self, now_utc: datetime) -> int:
        raise NotImplementedError

    def eod_window_open(self, d: date, *, eod_hour: int = 18, eod_minute: int = 0) -> datetime:
        raise NotImplementedError

    def is_eod_eligible(
        self,
        now_utc: datetime,
        *,
        eod_hour: int = 18,
        eod_minute: int = 0,
        last_eod_date: date | None = None,
    ) -> bool:
        raise NotImplementedError


class HistoricalMarketCalendar(MarketCalendar):
    def __init__(self, *, early_closes: dict[date, time] | None = None) -> None:
        self._early_closes = early_closes or {}

    @property
    def calendar_source(self) -> str:
        return "historical_weekday"

    def is_trading_day(self, value: date) -> bool:
        return value.weekday() < 5

    def market_open(self, value: date) -> datetime:
        return datetime.combine(value, time(9, 30), tzinfo=_ET).astimezone(UTC)

    def market_close(self, value: date) -> datetime:
        close_time = self._early_closes.get(value, time(16, 0))
        return datetime.combine(value, close_time, tzinfo=_ET).astimezone(UTC)

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

    def market_phase(self, now_utc: datetime) -> MarketPhase:
        d = now_utc.astimezone(_ET).date()
        if not self.is_trading_day(d):
            return MarketPhase.NON_TRADING_DAY
        open_ = self.market_open(d)
        close = self.market_close(d)
        if now_utc < open_:
            return MarketPhase.PRE_MARKET
        if now_utc < close:
            return MarketPhase.MARKET_HOURS
        return MarketPhase.POST_MARKET

    def seconds_until_next_session_open(self, now_utc: datetime) -> int:
        now_utc = _ensure_utc(now_utc)
        candidate = now_utc.astimezone(_ET).date()
        for _ in range(14):
            if self.is_trading_day(candidate):
                next_open = self.next_session_open(now_utc)
                if next_open is not None:
                    return max(0, int((next_open - now_utc).total_seconds()))
                next_open = self.market_open(candidate)
                return max(0, int((next_open - now_utc).total_seconds()))
            candidate += timedelta(days=1)
        return 86400

    def eod_window_open(self, d: date, *, eod_hour: int = 18, eod_minute: int = 0) -> datetime:
        return datetime.combine(d, time(eod_hour, eod_minute), tzinfo=_ET).astimezone(UTC)

    def is_eod_eligible(
        self,
        now_utc: datetime,
        *,
        eod_hour: int = 18,
        eod_minute: int = 0,
        last_eod_date: date | None = None,
    ) -> bool:
        now_et_date = now_utc.astimezone(_ET).date()
        if not self.is_trading_day(now_et_date):
            return False
        if self.market_phase(now_utc) != MarketPhase.POST_MARKET:
            return False
        eod_window = self.eod_window_open(now_et_date, eod_hour=eod_hour, eod_minute=eod_minute)
        if now_utc < eod_window:
            return False
        return last_eod_date != now_et_date


class FakeMarketCalendar(HistoricalMarketCalendar):
    pass


class RealMarketCalendar(HistoricalMarketCalendar):
    """Holiday-aware calendar for the live/paper soak loop.

    Uses exchange_calendars (XNYS schedule) for is_trading_day() so NYSE
    holidays are correctly excluded.  Session open/close times are inherited
    from HistoricalMarketCalendar (9:30 / 16:00 ET) — early-close days are
    not yet handled, acceptable for paper trading.

    HistoricalMarketCalendar remains weekday-only for replay/backtest so that
    historical data that pre-dates this library's coverage isn't affected.
    """

    @property
    def calendar_source(self) -> str:
        return "exchange_calendars:XNYS"

    def is_trading_day(self, value: date) -> bool:
        return bool(_nyse_calendar().is_session(value))

    def market_open(self, value: date) -> datetime:
        return _to_utc_datetime(_nyse_calendar().session_open(value))

    def market_close(self, value: date) -> datetime:
        return _to_utc_datetime(_nyse_calendar().session_close(value))

    def is_early_close(self, value: date) -> bool:
        if not self.is_trading_day(value):
            return False
        return self.market_close(value).astimezone(_ET).time() < time(16, 0)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_utc_datetime(value: object) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        raise TypeError(f"Expected datetime-compatible market timestamp, got {type(value)!r}")
    return _ensure_utc(value)

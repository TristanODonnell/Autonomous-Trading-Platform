"""AlpacaIntradayCoverageSource — how complete each candidate's five-minute bars are.

The candidate scorer ranks the pool on daily bars, which say nothing about how many
five-minute bars a name will have. Ingestion keeps a five-minute bar only when all five
of its one-minute bars exist, and on a single-exchange feed a high-priced share often
has minutes with no trade, so a name can rank near the top on daily volume and still
have a handful of bars a day.

This source fetches one-minute bars for the pool over the last sessions and counts, per
symbol, the five-minute buckets ingestion would have kept. It measures the data and
does not name a feed: on a full-market feed nearly every name comes out near 1.0.
"""

from __future__ import annotations

import time as time_module
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.universe.services.market_calendar_service import (
    MarketCalendarService,
)

logger = get_logger(__name__)

_EASTERN = ZoneInfo("America/New_York")
_FIVE_MINUTES = timedelta(minutes=5)


def five_minute_bucket(timestamp: datetime) -> datetime:
    """Start of the five-minute bucket a minute bar belongs to (ingestion's rule,
    ``BarAggregationService._get_bucket``)."""
    return timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0, microsecond=0)


class AlpacaIntradayCoverageSource:
    """Satisfies ``CandidateCoverageSource`` with Alpaca one-minute bars.

    Sessions are taken from the data: a day counts when any requested symbol has a
    regular-session bar on it and its close is at or before ``as_of``. A market holiday
    has no bars and so is not a session, whatever the static calendar knows about that
    year. The calendar is only asked for a day's open and close.

    Requests are paced (``min_batch_seconds``): the key's request allowance is shared
    with whatever else the account is doing. A batch that fails is retried once, then
    the error propagates, because a silently missing batch would reject its names.
    """

    source_name = "alpaca_minute"

    def __init__(
        self,
        *,
        data_client: Any | None = None,
        feed: Any | None = None,
        calendar: MarketCalendarService | None = None,
        batch_size: int = 20,
        min_batch_seconds: float = 3.0,
        sleep: Callable[[float], None] = time_module.sleep,
    ) -> None:
        self._data_client = data_client
        self._feed = feed
        self._calendar = calendar or MarketCalendarService()
        self._batch_size = max(int(batch_size), 1)
        self._min_batch_seconds = max(float(min_batch_seconds), 0.0)
        self._sleep = sleep

    def fetch_coverage(
        self, symbols: list[str], *, as_of: datetime, sessions: int
    ) -> dict[str, float]:
        if not symbols or sessions <= 0:
            return {}
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = self._data_client or self._default_client()
        feed = self._feed if self._feed is not None else self._default_feed()
        as_of_utc = _as_utc(as_of)
        # Enough calendar days to hold ``sessions`` sessions with holidays in between.
        window_start = datetime.combine(
            as_of_utc.date() - timedelta(days=ceil(sessions * 7 / 5) + 7), time.min, tzinfo=UTC
        )

        ordered = sorted(set(symbols))
        complete_by_symbol: dict[str, dict[date, int]] = {}
        session_days: set[date] = set()
        bounds: dict[date, tuple[datetime, datetime]] = {}
        n_batches = ceil(len(ordered) / self._batch_size)
        for i in range(n_batches):
            began = time_module.monotonic()
            batch = ordered[i * self._batch_size : (i + 1) * self._batch_size]
            request = StockBarsRequest(
                symbol_or_symbols=batch,
                start=window_start.replace(tzinfo=None),
                end=as_of_utc.replace(tzinfo=None),
                timeframe=TimeFrame.Minute,
                feed=feed,
            )
            bar_set = _with_one_retry(client.get_stock_bars, request, batch_index=i)
            data = getattr(bar_set, "data", None) or {}
            for symbol, bars in data.items():
                complete_by_symbol[symbol] = self._complete_buckets(
                    bars, as_of=as_of_utc, bounds=bounds, session_days=session_days
                )
            if i + 1 < n_batches:
                remaining = self._min_batch_seconds - (time_module.monotonic() - began)
                if remaining > 0:
                    self._sleep(remaining)

        chosen = sorted(session_days)[-sessions:]
        expected = sum(_expected_buckets(bounds[day]) for day in chosen)
        logger.info(
            "alpaca_intraday_coverage_source.fetched",
            extra={
                "symbols_requested": len(ordered),
                "symbols_with_bars": len(complete_by_symbol),
                "batches": n_batches,
                "sessions_requested": sessions,
                "sessions_measured": len(chosen),
                "first_session": chosen[0].isoformat() if chosen else None,
                "last_session": chosen[-1].isoformat() if chosen else None,
            },
        )
        if expected <= 0:
            return {symbol: 0.0 for symbol in ordered}
        return {
            symbol: sum(complete_by_symbol.get(symbol, {}).get(day, 0) for day in chosen) / expected
            for symbol in ordered
        }

    def _complete_buckets(
        self,
        bars: list[Any],
        *,
        as_of: datetime,
        bounds: dict[date, tuple[datetime, datetime]],
        session_days: set[date],
    ) -> dict[date, int]:
        """Per session day, the five-minute buckets that have all five minutes."""
        minutes: dict[date, dict[datetime, set[datetime]]] = defaultdict(lambda: defaultdict(set))
        for bar in bars:
            raw = getattr(bar, "timestamp", None)
            if raw is None:
                continue
            ts = _as_utc(raw)
            day = ts.astimezone(_EASTERN).date()
            if day not in bounds:
                bounds[day] = self._calendar.regular_session_utc(day)
            open_utc, close_utc = bounds[day]
            if not (open_utc <= ts < close_utc) or close_utc > as_of:
                continue
            session_days.add(day)
            minutes[day][five_minute_bucket(ts)].add(ts.replace(second=0, microsecond=0))
        return {
            day: sum(1 for seen in buckets.values() if len(seen) == 5)
            for day, buckets in minutes.items()
        }

    @staticmethod
    def _default_client() -> Any:
        from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
            get_stock_historical_client,
        )

        return get_stock_historical_client()

    @staticmethod
    def _default_feed() -> Any:
        from autonomous_trading_platform.ingestion.market_data.clients.alpaca_market_data_client import (
            get_data_feed,
        )

        return get_data_feed()


def _expected_buckets(session_bounds: tuple[datetime, datetime]) -> int:
    open_utc, close_utc = session_bounds
    return int((close_utc - open_utc) / _FIVE_MINUTES)


def _with_one_retry(call: Any, *args: Any, batch_index: int) -> Any:
    try:
        return call(*args)
    except Exception as exc:
        logger.warning(
            "alpaca_intraday_coverage_source.batch_retry",
            extra={"batch_index": batch_index, "error": str(exc)},
        )
        return call(*args)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

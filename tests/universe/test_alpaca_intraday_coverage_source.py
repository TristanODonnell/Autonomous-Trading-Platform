"""AlpacaIntradayCoverageSource: the share of five-minute bars each candidate had over
recent sessions, counted from one-minute bars the way ingestion keeps them (a bucket
needs all five minutes)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from autonomous_trading_platform.ingestion.market_data.services.bar_aggregation_service import (
    BarAggregationService,
)
from autonomous_trading_platform.universe.providers.alpaca_intraday_coverage_source import (
    AlpacaIntradayCoverageSource,
    five_minute_bucket,
)
from autonomous_trading_platform.universe.types import CandidateCoverageSource

_NY = ZoneInfo("America/New_York")
# Monday to Friday; the following Monday 00:00 UTC is the pick date.
_WEEK = [date(2024, 11, 11) + timedelta(days=d) for d in range(5)]
_AS_OF = datetime(2024, 11, 18, tzinfo=UTC)
_FULL_SESSION = range(390)


def _bars(day: date, minutes: Iterable[int]) -> list[Any]:
    """One-minute bars at the given minutes after the 09:30 New York open."""
    opening = datetime.combine(day, time(9, 30), tzinfo=_NY).astimezone(UTC)
    return [SimpleNamespace(timestamp=opening + timedelta(minutes=m)) for m in minutes]


def _week(minutes: Iterable[int], days: Iterable[date] = _WEEK) -> list[Any]:
    minutes = list(minutes)
    return [bar for day in days for bar in _bars(day, minutes)]


class _FakeDataClient:
    def __init__(self, bars: dict[str, list[Any]], *, fail_first: int = 0) -> None:
        self._bars = bars
        self.requests: list[Any] = []
        self._fail_first = fail_first

    def get_stock_bars(self, request: Any) -> Any:
        self.requests.append(request)
        if self._fail_first > 0:
            self._fail_first -= 1
            raise RuntimeError("alpaca 5xx")
        symbols = request.symbol_or_symbols
        return SimpleNamespace(data={s: self._bars[s] for s in symbols if s in self._bars})


def _source(
    bars: dict[str, list[Any]], **kwargs: Any
) -> tuple[AlpacaIntradayCoverageSource, _FakeDataClient]:
    client = _FakeDataClient(bars, fail_first=kwargs.pop("fail_first", 0))
    kwargs.setdefault("min_batch_seconds", 0.0)
    return AlpacaIntradayCoverageSource(data_client=client, feed="iex", **kwargs), client


def _coverage(bars: dict[str, list[Any]], *, as_of: datetime = _AS_OF, sessions: int = 20) -> Any:
    source, _ = _source(bars)
    return source.fetch_coverage(sorted(bars), as_of=as_of, sessions=sessions)


def test_satisfies_the_protocol() -> None:
    source: CandidateCoverageSource = _source({})[0]
    assert source.source_name == "alpaca_minute"


def test_the_bucket_rule_is_ingestions() -> None:
    start = datetime(2024, 11, 11, 14, 30, tzinfo=UTC)
    for minute in range(0, 390, 7):
        ts = start + timedelta(minutes=minute, seconds=12)
        assert five_minute_bucket(ts) == BarAggregationService._get_bucket(ts)


def test_coverage_is_complete_buckets_over_a_full_sessions_buckets() -> None:
    coverage = _coverage(
        {
            "FULL": _week(_FULL_SESSION),
            # The first half of every session.
            "HALF": _week(range(195)),
            # Every bucket is missing its last minute: ingestion keeps none of them.
            "GAPS": _week(m for m in _FULL_SESSION if m % 5 != 4),
        }
    )
    assert coverage == {"FULL": 1.0, "GAPS": 0.0, "HALF": pytest.approx(39 / 78)}


def test_a_repeated_minute_does_not_complete_a_bucket() -> None:
    four_minutes_one_twice = [0, 1, 2, 3, 3]
    coverage = _coverage(
        {"FULL": _week(_FULL_SESSION), "DUP": _week([*four_minutes_one_twice, *range(5, 390)])}
    )
    assert coverage["DUP"] == pytest.approx(77 / 78)


def test_bars_outside_the_regular_session_are_not_counted() -> None:
    before_open_and_after_close = [*range(-30, 0), *range(390, 420)]
    coverage = _coverage(
        {"FULL": _week(_FULL_SESSION), "EXTENDED": _week(before_open_and_after_close)}
    )
    assert coverage["EXTENDED"] == 0.0


def test_a_symbol_the_provider_has_nothing_for_is_zero() -> None:
    source, _ = _source({"FULL": _week(_FULL_SESSION)})
    assert source.fetch_coverage(["FULL", "NONE"], as_of=_AS_OF, sessions=20) == {
        "FULL": 1.0,
        "NONE": 0.0,
    }


def test_only_the_last_sessions_asked_for_are_measured() -> None:
    monday_to_wednesday, thursday_friday = _WEEK[:3], _WEEK[3:]
    coverage = _coverage(
        {
            "EARLY": _week(_FULL_SESSION, monday_to_wednesday),
            "LATE": _week(_FULL_SESSION, thursday_friday),
        },
        sessions=2,
    )
    assert coverage == {"EARLY": 0.0, "LATE": 1.0}


def test_a_session_still_open_at_as_of_is_left_out() -> None:
    friday_midday = datetime(2024, 11, 15, 17, 0, tzinfo=UTC)
    coverage = _coverage(
        {
            "FULL": _week(_FULL_SESSION),
            # Bars on Friday only: none of them belong to a closed session yet.
            "FRIDAY": _week(_FULL_SESSION, [_WEEK[4]]),
        },
        as_of=friday_midday,
    )
    assert coverage == {"FULL": 1.0, "FRIDAY": 0.0}


def test_a_day_nobody_traded_is_not_a_session() -> None:
    # Wednesday has no bars for any symbol (a holiday the static calendar may not list).
    days = [d for d in _WEEK if d != _WEEK[2]]
    assert _coverage({"FULL": _week(_FULL_SESSION, days)}) == {"FULL": 1.0}


def test_an_early_close_is_judged_against_its_shorter_session() -> None:
    half_day = date(2024, 11, 29)  # 13:00 New York close: 42 five-minute bars
    coverage = _coverage(
        {"FULL": _bars(half_day, range(210)), "HALF": _bars(half_day, range(105))},
        as_of=datetime(2024, 11, 30, tzinfo=UTC),
    )
    assert coverage == {"FULL": 1.0, "HALF": pytest.approx(21 / 42)}


def test_summer_sessions_use_the_daylight_saving_open() -> None:
    summer_day = date(2025, 6, 10)  # 13:30 UTC open
    coverage = _coverage(
        {"FULL": _bars(summer_day, _FULL_SESSION)}, as_of=datetime(2025, 6, 11, tzinfo=UTC)
    )
    assert coverage == {"FULL": 1.0}


def test_requests_minute_bars_in_batches_for_the_window_before_as_of() -> None:
    source, client = _source({"AAA": _week(_FULL_SESSION)}, batch_size=2)

    source.fetch_coverage(["CCC", "AAA", "BBB", "AAA"], as_of=_AS_OF, sessions=20)

    assert [list(r.symbol_or_symbols) for r in client.requests] == [["AAA", "BBB"], ["CCC"]]
    first = client.requests[0]
    assert str(first.timeframe) == "1Min"
    assert first.feed == "iex"
    # 20 sessions: 28 calendar days plus a week for holidays.
    assert first.start == datetime(2024, 10, 14)
    assert first.end == datetime(2024, 11, 18)


def test_nothing_is_requested_for_an_empty_pool_or_no_sessions() -> None:
    source, client = _source({})
    assert source.fetch_coverage([], as_of=_AS_OF, sessions=20) == {}
    assert source.fetch_coverage(["AAA"], as_of=_AS_OF, sessions=0) == {}
    assert client.requests == []


def test_a_failed_batch_is_retried_once_then_raises() -> None:
    source, client = _source({"AAA": _week(_FULL_SESSION)}, fail_first=1)
    assert source.fetch_coverage(["AAA"], as_of=_AS_OF, sessions=20) == {"AAA": 1.0}
    assert len(client.requests) == 2

    failing, _ = _source({}, fail_first=2)
    with pytest.raises(RuntimeError, match="alpaca 5xx"):
        failing.fetch_coverage(["AAA"], as_of=_AS_OF, sessions=20)


def test_batches_are_paced_but_the_last_one_does_not_wait() -> None:
    waits: list[float] = []
    source, client = _source(
        {"AAA": _week(_FULL_SESSION)}, batch_size=1, min_batch_seconds=3.0, sleep=waits.append
    )

    source.fetch_coverage(["AAA", "BBB", "CCC"], as_of=_AS_OF, sessions=20)

    assert len(client.requests) == 3
    assert len(waits) == 2
    assert all(0.0 < wait <= 3.0 for wait in waits)

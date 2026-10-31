"""AlpacaDailyBarSource: daily bars for candidate scoring, fetched per run (Finding 16)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from autonomous_trading_platform.universe.providers.alpaca_daily_bar_source import (
    AlpacaDailyBarSource,
)
from autonomous_trading_platform.universe.types import CandidateBarSource

_START = datetime(2026, 9, 1, tzinfo=UTC)
_END = datetime(2026, 10, 30, 22, 0, tzinfo=UTC)


def _bar(day: int, close: float, volume: int, trade_count: int | None = 10) -> Any:
    return SimpleNamespace(
        timestamp=datetime(2026, 10, day, 4, 0, tzinfo=UTC),
        close=close,
        volume=volume,
        trade_count=trade_count,
    )


class _FakeDataClient:
    """Records each StockBarsRequest and serves bars per symbol."""

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


def test_satisfies_the_protocol_with_one_bar_per_day() -> None:
    source: CandidateBarSource = AlpacaDailyBarSource(data_client=_FakeDataClient({}), feed="iex")
    assert source.source_name == "alpaca_daily"
    assert source.expected_bars_per_day == 1


def test_fetches_in_batches_and_returns_ordered_rows() -> None:
    client = _FakeDataClient(
        {
            "AAPL": [_bar(2, 101.0, 1000), _bar(1, 100.0, 900)],
            "MSFT": [_bar(1, 300.0, 500, trade_count=None)],
            # NVDA: in the pool but Alpaca returns nothing -> absent from the result
        }
    )
    source = AlpacaDailyBarSource(data_client=client, feed="iex", batch_size=2)

    rows = source.fetch_bars(["NVDA", "AAPL", "MSFT"], start=_START, end=_END)

    assert sorted(rows) == ["AAPL", "MSFT"]
    assert [r[0].day for r in rows["AAPL"]] == [1, 2]
    assert rows["AAPL"][0] == (datetime(2026, 10, 1, 4, 0, tzinfo=UTC), 100.0, 900, 10)
    assert rows["MSFT"][0][3] is None
    # 3 symbols, batch_size 2 -> 2 requests, sorted symbols, daily timeframe, chosen feed.
    assert [list(r.symbol_or_symbols) for r in client.requests] == [["AAPL", "MSFT"], ["NVDA"]]
    first = client.requests[0]
    assert str(first.timeframe) == "1Day"
    assert first.feed == "iex"
    assert first.start.tzinfo is None and first.start == _START.replace(tzinfo=None)
    assert first.end == _END.replace(tzinfo=None)


def test_a_failed_batch_is_retried_once_then_raises() -> None:
    bars = {"AAPL": [_bar(1, 100.0, 900)]}
    recovered = _FakeDataClient(bars, fail_first=1)
    rows = AlpacaDailyBarSource(data_client=recovered, feed="iex").fetch_bars(
        ["AAPL"], start=_START, end=_END
    )
    assert list(rows) == ["AAPL"] and len(recovered.requests) == 2

    broken = _FakeDataClient(bars, fail_first=2)
    with pytest.raises(RuntimeError, match="alpaca 5xx"):
        AlpacaDailyBarSource(data_client=broken, feed="iex").fetch_bars(
            ["AAPL"], start=_START, end=_END
        )
    assert len(broken.requests) == 2  # no third attempt, no silent partial result


def test_empty_symbol_list_makes_no_request() -> None:
    client = _FakeDataClient({})
    assert (
        AlpacaDailyBarSource(data_client=client, feed="iex").fetch_bars([], start=_START, end=_END)
        == {}
    )
    assert client.requests == []

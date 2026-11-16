"""Inside ``reuse_bars`` a pipeline run parses each symbol's bars once and cuts every
simulation's window from them. What a simulation is given must not depend on it: the
same bars as a fresh read, whatever was loaded before."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from itertools import permutations
from typing import Any

import pyarrow as pa
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
    SimulationWindowLoader,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader

_SYMBOLS = ("AAA", "BBB")
_BARS_PER_SESSION = 78
# Two weeks of sessions, Monday 2024-06-03 to Friday 2024-06-14.
_SESSIONS = [date(2024, 6, 3) + timedelta(days=d) for d in range(12) if d % 7 < 5]
_START = date(2024, 6, 12)  # a Wednesday, so every warmup below finds earlier sessions
_END = date(2024, 6, 14)
# Read starts 2024-06-10, 06-09 and 06-06: three different reads for one window.
_WARMUPS = (20, 100, 300)


def _rows(symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in _SESSIONS:
        opening = datetime(session.year, session.month, session.day, 13, 30, tzinfo=UTC)
        for i in range(_BARS_PER_SESSION):
            ts = opening + timedelta(minutes=5 * i)
            price = 100.0 + len(rows) * 0.01
            rows.append(
                {
                    "bar_id": f"{symbol}-{ts.isoformat()}",
                    "timestamp": ts,
                    "end_timestamp": ts + timedelta(minutes=5),
                    "interval": BarInterval.FIVE_MIN.value,
                    "symbol": symbol,
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price + 0.1,
                    "volume": 1_000 + i,
                    "price_basis": PriceBasis.RAW.value,
                    "adjustment_factor": 1.0,
                    "source": "test",
                    "ingested_at": ts,
                }
            )
    return rows


class _CountingReader:
    """The Parquet reader's date rule over rows held in memory, counting each read."""

    def __init__(self) -> None:
        self.rows = {symbol: _rows(symbol) for symbol in _SYMBOLS}
        self.reads: list[tuple[str, date, date]] = []
        self.before_read: Callable[[], None] | None = None

    def read(
        self,
        *,
        dataset: object,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
        engine: str = "pyarrow",
    ) -> pa.Table:
        if self.before_read is not None:
            self.before_read()
        self.reads.append((symbol, start_date, end_date))
        start_ts, end_ts = HistoricalBarDatasetReader._date_range_to_timestamps(
            start_date, end_date
        )
        return pa.Table.from_pylist(
            [r for r in self.rows.get(symbol, []) if start_ts <= r["timestamp"] < end_ts]
        )


def _loader() -> tuple[SimulationWindowLoader, _CountingReader]:
    reader = _CountingReader()
    return SimulationWindowLoader(bar_reader=reader), reader  # type: ignore[arg-type]


def _load(
    loader: SimulationWindowLoader,
    *,
    warmup: int = 0,
    end: date = _END,
    symbols: tuple[str, ...] = _SYMBOLS,
    strict: bool = False,
) -> SimulationWindowData:
    return loader.load_window(
        dataset_version="v",
        bars_dataset=RAW_BARS_DATASET,
        symbols=symbols,
        start_date=_START,
        end_date=end,
        warmup_bars=warmup,
        strict=strict,
    )


def _fresh(**kwargs: Any) -> SimulationWindowData:
    """The same request on a loader that has never loaded anything."""
    return _load(_loader()[0], **kwargs)


def _assert_same_window(got: SimulationWindowData, want: SimulationWindowData) -> None:
    assert got.timeline == want.timeline
    assert got.bars_by_symbol == want.bars_by_symbol
    assert got.bars_by_timestamp == want.bars_by_timestamp
    assert got.warmup_bars_by_symbol == want.warmup_bars_by_symbol
    assert got.warmup_timestamps == want.warmup_timestamps
    assert got.sorted_timestamps_by_symbol == want.sorted_timestamps_by_symbol


def test_the_fixture_gives_each_warmup_its_own_read() -> None:
    loader, reader = _loader()
    for warmup in _WARMUPS:
        window = _load(loader, warmup=warmup, symbols=("AAA",))
        assert len(window.warmup_bars_by_symbol["AAA"]) == warmup
    assert len({start for _, start, _ in reader.reads}) == len(_WARMUPS)


def test_outside_the_block_every_load_reads_parquet() -> None:
    loader, reader = _loader()

    _load(loader, warmup=20)
    _load(loader, warmup=20)

    assert len(reader.reads) == 2 * len(_SYMBOLS)
    assert (loader.bar_reads, loader.bar_reuses) == (0, 0)
    assert loader._loaded is None


def test_a_repeated_window_is_read_once() -> None:
    loader, reader = _loader()

    with loader.reuse_bars():
        first = _load(loader, warmup=20)
        second = _load(loader, warmup=20)

    assert len(reader.reads) == len(_SYMBOLS)
    assert (loader.bar_reads, loader.bar_reuses) == (len(_SYMBOLS), len(_SYMBOLS))
    _assert_same_window(first, _fresh(warmup=20))
    _assert_same_window(second, first)


@pytest.mark.parametrize("order", list(permutations((0, *_WARMUPS))))
def test_any_order_of_warmups_gives_what_a_fresh_read_gives(order: tuple[int, ...]) -> None:
    loader, _ = _loader()

    with loader.reuse_bars():
        for warmup in order:
            _assert_same_window(_load(loader, warmup=warmup), _fresh(warmup=warmup))


def test_a_longer_warmup_reads_again_and_a_shorter_one_does_not() -> None:
    loader, reader = _loader()

    with loader.reuse_bars():
        _load(loader, warmup=20)
        after_short = len(reader.reads)
        _load(loader, warmup=300)
        after_long = len(reader.reads)
        _load(loader, warmup=100)
        _load(loader, warmup=20)
        _load(loader)

    assert after_short == len(_SYMBOLS)
    assert after_long == 2 * len(_SYMBOLS)
    assert len(reader.reads) == after_long


def test_a_new_symbol_in_the_same_window_is_the_only_one_read() -> None:
    loader, reader = _loader()

    with loader.reuse_bars():
        _load(loader, warmup=20, symbols=("AAA",))
        both = _load(loader, warmup=20)

    assert [symbol for symbol, _, _ in reader.reads] == ["AAA", "BBB"]
    _assert_same_window(both, _fresh(warmup=20))


def test_one_window_is_held_and_the_previous_one_is_dropped_before_the_next_read() -> None:
    loader, reader = _loader()
    held_at_read: list[tuple[date, list[str]]] = []

    def _record() -> None:
        assert loader._loaded is not None
        held_at_read.append((loader._loaded.window[2], sorted(loader._loaded.by_symbol)))

    with loader.reuse_bars():
        _load(loader, warmup=20)
        reader.before_read = _record
        earlier_end = _load(loader, warmup=20, end=date(2024, 6, 13))

        assert loader._loaded is not None
        assert loader._loaded.window[2] == date(2024, 6, 13)

    # At the first read for the new end date nothing of the old window was left.
    assert held_at_read[0] == (date(2024, 6, 13), [])
    _assert_same_window(earlier_end, _fresh(warmup=20, end=date(2024, 6, 13)))


def test_bars_are_released_when_the_outermost_block_ends() -> None:
    loader, reader = _loader()

    with loader.reuse_bars():
        with loader.reuse_bars():
            _load(loader, warmup=20)
        assert loader._loaded is not None
        _load(loader, warmup=20)
        assert len(reader.reads) == len(_SYMBOLS)

    assert loader._loaded is None
    _load(loader, warmup=20)
    assert len(reader.reads) == 2 * len(_SYMBOLS)


def test_bars_are_released_when_the_block_ends_with_an_error() -> None:
    loader, _ = _loader()

    with pytest.raises(RuntimeError, match="boom"), loader.reuse_bars():
        _load(loader, warmup=20)
        raise RuntimeError("boom")

    assert loader._loaded is None and loader._reuse_depth == 0


def test_a_run_that_changes_its_lists_does_not_change_the_next_run() -> None:
    loader, _ = _loader()

    with loader.reuse_bars():
        first = _load(loader, warmup=20)
        for bars in first.bars_by_symbol.values():
            bars.clear()
        first.bars_by_timestamp.clear()
        first.timeline.clear()

        _assert_same_window(_load(loader, warmup=20), _fresh(warmup=20))


@pytest.mark.parametrize("reuse", [False, True])
def test_strict_still_rejects_a_symbol_with_no_bars(reuse: bool) -> None:
    loader, _ = _loader()

    def _strict_load() -> None:
        _load(loader, warmup=20, symbols=("AAA", "ZZZ"), strict=True)

    with pytest.raises(ValueError, match="No bar data found for symbol=ZZZ"):
        if reuse:
            with loader.reuse_bars():
                _strict_load()
        else:
            _strict_load()

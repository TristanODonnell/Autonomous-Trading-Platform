"""A simulation is given each symbol's last ``warmup_bars`` bars before its window,
wherever they lie (plan R17). The loader used to reach back a number of calendar days
worked out from 78 bars a session and five sessions a week, which found nothing on a
Monday, after a holiday, or for a name that trades in fewer slots than that."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pyarrow as pa
import pytest

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.research.simulation.services.simulation_window_loader_service import (
    SimulationWindowData,
    SimulationWindowLoader,
    shuffle_window_bar_timestamps,
)
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader

_HOLIDAY = date(2024, 5, 27)  # Memorial Day, a Monday
# Six weeks of sessions, Monday 2024-05-06 to Friday 2024-06-14, without the holiday.
_SESSIONS = [
    day
    for day in (date(2024, 5, 6) + timedelta(days=d) for d in range(40))
    if day.weekday() < 5 and day != _HOLIDAY
]
_END = date(2024, 6, 14)
_MONDAY = date(2024, 6, 10)
_AFTER_HOLIDAY = date(2024, 5, 28)  # the previous session is Friday 2024-05-24
_WEDNESDAY = date(2024, 6, 12)

_FULL = "FULL"  # every five-minute slot of every session
_THIN = "THIN"  # 30 bars a session, like the thinnest names on the feed
_GAPPY = "GAPPY"  # no bars for the three weeks before 2024-06-10
_LATE = "LATE"  # its history begins on 2024-06-10
_BARS_PER_SESSION = {_FULL: 78, _THIN: 30, _GAPPY: 78, _LATE: 78}
_GAP = (date(2024, 5, 20), date(2024, 6, 7))
_LATE_FIRST_SESSION = date(2024, 6, 10)

_WARMUPS = (20, 61, 78, 100, 201)


def _has_bars(symbol: str, session: date) -> bool:
    if symbol == _GAPPY:
        return not _GAP[0] <= session <= _GAP[1]
    if symbol == _LATE:
        return session >= _LATE_FIRST_SESSION
    return True


def _rows(symbol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in _SESSIONS:
        if not _has_bars(symbol, session):
            continue
        opening = datetime(session.year, session.month, session.day, 13, 30, tzinfo=UTC)
        for i in range(_BARS_PER_SESSION[symbol]):
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


_ROWS = {symbol: _rows(symbol) for symbol in _BARS_PER_SESSION}


def _stamps_before(symbol: str, start: date) -> list[datetime]:
    """Every bar time the symbol has before ``start``, oldest first."""
    return [r["timestamp"] for r in _ROWS[symbol] if r["timestamp"].date() < start]


class _Reader:
    """The Parquet reader's date rule over rows held in memory, recording each read."""

    def __init__(self, *, fail_for: str | None = None) -> None:
        self.reads: list[tuple[str, date, date]] = []
        self.fail_for = fail_for

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
        self.reads.append((symbol, start_date, end_date))
        if symbol == self.fail_for:
            raise OSError("disk gone")
        start_ts, end_ts = HistoricalBarDatasetReader._date_range_to_timestamps(
            start_date, end_date
        )
        return pa.Table.from_pylist(
            [r for r in _ROWS.get(symbol, []) if start_ts <= r["timestamp"] < end_ts]
        )

    def window_reads(self, start: date) -> list[tuple[str, date, date]]:
        return [read for read in self.reads if read[2] >= start]

    def counting_reads(self, start: date) -> list[tuple[str, date, date]]:
        return [read for read in self.reads if read[2] < start]


def _load(
    start: date,
    warmup: int,
    *symbols: str,
    reader: _Reader | None = None,
) -> SimulationWindowData:
    loader = SimulationWindowLoader(bar_reader=reader or _Reader())  # type: ignore[arg-type]
    return loader.load_window(
        dataset_version="v",
        bars_dataset=RAW_BARS_DATASET,
        symbols=symbols,
        start_date=start,
        end_date=_END,
        warmup_bars=warmup,
    )


def _warmup_stamps(window: SimulationWindowData, symbol: str) -> list[datetime]:
    return [bar.timestamp for bar in window.warmup_bars_by_symbol.get(symbol, [])]


@pytest.mark.parametrize("warmup", _WARMUPS)
def test_a_monday_start_gets_the_last_bars_before_the_weekend(warmup: int) -> None:
    window = _load(_MONDAY, warmup, _FULL)

    assert _warmup_stamps(window, _FULL) == _stamps_before(_FULL, _MONDAY)[-warmup:]
    assert len(window.warmup_bars_by_symbol[_FULL]) == warmup


@pytest.mark.parametrize("warmup", _WARMUPS)
def test_a_start_after_a_holiday_gets_the_bars_from_before_it(warmup: int) -> None:
    window = _load(_AFTER_HOLIDAY, warmup, _FULL)

    stamps = _warmup_stamps(window, _FULL)
    assert stamps == _stamps_before(_FULL, _AFTER_HOLIDAY)[-warmup:]
    assert stamps[-1].date() == date(2024, 5, 24)


@pytest.mark.parametrize("warmup", _WARMUPS)
def test_a_thin_name_gets_its_own_bars_from_as_many_sessions_as_it_takes(warmup: int) -> None:
    window = _load(_WEDNESDAY, warmup, _THIN, _FULL)

    # 201 bars of a 30-bar name are seven sessions; of a full name, three.
    for symbol in (_THIN, _FULL):
        assert _warmup_stamps(window, symbol) == _stamps_before(symbol, _WEDNESDAY)[-warmup:]
        assert len(window.warmup_bars_by_symbol[symbol]) == warmup


def test_a_gap_in_a_names_history_does_not_end_the_search() -> None:
    window = _load(_MONDAY, 100, _GAPPY)

    stamps = _warmup_stamps(window, _GAPPY)
    assert len(stamps) == 100
    assert stamps == _stamps_before(_GAPPY, _MONDAY)[-100:]
    assert stamps[-1].date() == date(2024, 5, 17)  # the last session before the gap


def test_a_name_with_less_history_than_the_warmup_gets_every_earlier_bar() -> None:
    tuesday = date(2024, 6, 11)

    window = _load(tuesday, 201, _LATE)

    assert len(window.warmup_bars_by_symbol[_LATE]) == 78  # its one earlier session
    assert _warmup_stamps(window, _LATE) == _stamps_before(_LATE, tuesday)


def test_a_name_with_no_earlier_bars_starts_without_a_warmup() -> None:
    window = _load(_LATE_FIRST_SESSION, 201, _LATE, _FULL)

    assert _LATE not in window.warmup_bars_by_symbol
    assert window.bars_by_symbol[_LATE][0].timestamp.date() == _LATE_FIRST_SESSION
    # The other name in the same load is not affected.
    assert len(window.warmup_bars_by_symbol[_FULL]) == 201


@pytest.mark.parametrize("start", [day for day in _SESSIONS if day >= date(2024, 5, 13)])
def test_every_start_gives_each_name_the_bars_that_exist_up_to_the_warmup(start: date) -> None:
    symbols = (_FULL, _THIN, _GAPPY, _LATE)

    for warmup in _WARMUPS:
        window = _load(start, warmup, *symbols)
        for symbol in symbols:
            assert _warmup_stamps(window, symbol) == _stamps_before(symbol, start)[-warmup:], (
                f"{symbol} from {start} with a warmup of {warmup}"
            )


@pytest.mark.parametrize("warmup", _WARMUPS)
def test_the_bars_inside_the_window_do_not_depend_on_the_warmup(warmup: int) -> None:
    without = _load(_MONDAY, 0, _FULL, _THIN)
    with_warmup = _load(_MONDAY, warmup, _FULL, _THIN)

    for symbol in (_FULL, _THIN):
        inside = [b for b in with_warmup.bars_by_symbol[symbol] if b.timestamp.date() >= _MONDAY]
        assert inside == without.bars_by_symbol[symbol]
    assert all(stamp.date() < _MONDAY for stamp in with_warmup.warmup_timestamps)
    assert not without.warmup_timestamps


def test_a_warmup_the_estimate_covers_reads_from_the_same_date_as_before() -> None:
    # 20 bars on a Wednesday: the estimate is two calendar days, Monday, and a full
    # name has them there. One count, and the window read starts where it used to.
    reader = _Reader()

    _load(_WEDNESDAY, 20, _FULL, reader=reader)

    assert reader.counting_reads(_WEDNESDAY) == [(_FULL, date(2024, 6, 10), date(2024, 6, 11))]
    assert reader.window_reads(_WEDNESDAY) == [(_FULL, date(2024, 6, 10), _END)]


def test_the_window_is_read_once_however_far_back_the_search_goes() -> None:
    reader = _Reader()

    _load(_MONDAY, 100, _GAPPY, reader=reader)

    assert len(reader.counting_reads(_MONDAY)) > 1
    assert len(reader.window_reads(_MONDAY)) == 1
    # The search doubles its reach, so each count starts earlier than the last.
    starts = [read_start for _, read_start, _ in reader.counting_reads(_MONDAY)]
    assert starts == sorted(starts, reverse=True)
    assert len(set(starts)) == len(starts)


def test_the_search_stops_at_the_earliest_date_when_there_is_no_history() -> None:
    reader = _Reader()

    _load(_LATE_FIRST_SESSION, 20, _LATE, reader=reader)

    earliest = SimulationWindowLoader._EARLIEST_BAR_DATE
    counting = reader.counting_reads(_LATE_FIRST_SESSION)
    assert counting[-1][1] == earliest
    assert all(read_start >= earliest for _, read_start, _ in counting)
    assert reader.window_reads(_LATE_FIRST_SESSION) == [(_LATE, earliest, _END)]


def test_no_warmup_asked_means_no_count_and_a_read_from_the_start() -> None:
    reader = _Reader()

    _load(_MONDAY, 0, _FULL, reader=reader)

    assert reader.reads == [(_FULL, _MONDAY, _END)]


def test_a_window_whose_names_all_got_their_warmup_reports_no_shortfall() -> None:
    # A Monday, a holiday, a thin name and a gap: none of them is a short history.
    for start in (_MONDAY, _AFTER_HOLIDAY, _WEDNESDAY):
        window = _load(start, 201, _FULL, _THIN, _GAPPY)
        assert window.warmup_shortfall_by_symbol == {}


def test_a_name_with_less_history_than_the_warmup_is_reported_with_the_bars_missing() -> None:
    tuesday = date(2024, 6, 11)

    window = _load(tuesday, 201, _LATE, _FULL)

    # LATE has one earlier session of 78 bars; FULL has all 201.
    assert window.warmup_shortfall_by_symbol == {_LATE: 201 - 78}


def test_a_name_with_no_earlier_bars_is_short_by_the_whole_warmup() -> None:
    window = _load(_LATE_FIRST_SESSION, 20, _LATE, _FULL)

    assert window.warmup_shortfall_by_symbol == {_LATE: 20}


def test_no_warmup_asked_reports_no_shortfall() -> None:
    assert _load(_LATE_FIRST_SESSION, 0, _LATE).warmup_shortfall_by_symbol == {}


def test_shuffling_a_window_keeps_its_shortfall() -> None:
    window = _load(date(2024, 6, 11), 201, _LATE)

    shuffled = shuffle_window_bar_timestamps(window, random_seed=7)

    assert shuffled.warmup_shortfall_by_symbol == {_LATE: 201 - 78}


def test_a_failed_count_is_reported_like_a_failed_read() -> None:
    reader = _Reader(fail_for=_FULL)

    with pytest.raises(RuntimeError, match=f"Failed to load bar data for symbol={_FULL}"):
        _load(_MONDAY, 20, _FULL, reader=reader)

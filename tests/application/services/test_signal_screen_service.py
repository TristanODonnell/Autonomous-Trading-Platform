"""What the signal screen is handed: which days, which names, and no holdout day."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.application.services.signal_screen_service import (
    SignalScreenError,
    SignalScreenSource,
    run_signal_screen,
)
from autonomous_trading_platform.contracts.runtime.research_window_dataset import (
    ResearchWindowDataset,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.research.signal_screen.library import (
    Condition,
    Library,
    Row,
    reading,
)
from autonomous_trading_platform.research.signal_screen.screen import ScreenSettings

# A week of bars before the selection piece (2024-11-18), and a month into the holdout.
FIRST_DAY, LAST_DAY = date(2024, 11, 11), date(2026, 6, 12)
SELECTION_FIRST, EVALUATION_FIRST = date(2024, 11, 18), date(2025, 8, 18)
LAST_READ = date(2026, 5, 15)  # the Friday before the holdout
LEFT_OUT = date(2025, 3, 10)  # a session the dataset left out by name
# D and E leave the list, F and G join: at noon in New York, inside a session.
ROTATION = datetime(2025, 6, 2, 16, 0, tzinfo=UTC)
SLOTS = (0, 26, 52, 77)  # four bars a day keep the test quick
LATE_SLOTS = (52, 77)  # C trades thinly: its first bar of a day comes after the rotation

LIBRARY = Library(
    "plumbing",
    (
        Row(
            id="5m.up",
            family="test",
            scale="5min",
            conditions=(Condition(reading("rate_of_change", lookback=2), ">", 0.0),),
            direction=1,
            reads="up",
        ),
        Row(
            id="d.up",
            family="test",
            scale="daily",
            conditions=(Condition(reading("rate_of_change", lookback=1), ">", 0.0),),
            direction=1,
            reads="up",
        ),
    ),
)
SETTINGS = ScreenSettings(shuffles=60, seed=3)


def _weekdays(first: date, last: date) -> list[date]:
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return [day for day in days if day.weekday() < 5]


def _sessions_between(first: date, last: date) -> list[date]:
    return [day for day in _weekdays(first, last) if day != LEFT_OUT]


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _timeline() -> UniverseTimeline:
    def period(version: str, start: datetime, end: datetime | None, symbols: str):
        return UniverseTimelinePeriod(
            universe_version_id=version,
            effective_from=start,
            effective_to=end,
            members=[UniverseTimelineMember(symbol=symbol) for symbol in symbols],
        )

    return UniverseTimeline(
        dataset_version_id="universe_timeline_test",
        window_start=date(2024, 11, 1),
        content_hash="abc123",
        periods=[
            period("u1", _midnight(date(2024, 11, 1)), ROTATION, "ABCDE"),
            period("u2", ROTATION, None, "ABCFG"),
        ],
    )


def _bars(symbol: str, first: date, last: date) -> pd.DataFrame:
    """Bars on every weekday asked for (the left-out session's too: the feed has them)."""
    rng = np.random.default_rng(ord(symbol))
    stamps = [
        pd.Timestamp(day, tz="America/New_York") + pd.Timedelta(minutes=9 * 60 + 30 + 5 * slot)
        for day in _weekdays(first, last)
        for slot in (LATE_SLOTS if symbol == "C" else SLOTS)
    ]
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.002, len(stamps)))
    return pd.DataFrame(
        {
            "timestamp": pd.DatetimeIndex(stamps).tz_convert("UTC"),
            "close": closes,
            "volume": rng.integers(100, 900, len(stamps)).astype(float),
        }
    )


def _source(*, read_bars=None, first_day: date = FIRST_DAY, calls: list | None = None):
    def reading_bars(symbol: str, first: date, last: date) -> pd.DataFrame:
        if calls is not None:
            calls.append((symbol, first, last))
        return (read_bars or _bars)(symbol, first, last)

    return SignalScreenSource(
        dataset=ResearchWindowDataset(
            dataset_version_id="raw_bars_test",
            first_day=first_day,
            last_day=LAST_DAY,
            symbols=tuple("ABCDEF"),  # G is on the list and not in the dataset
            excluded_sessions=(LEFT_OUT,),
        ),
        timeline=_timeline(),
        read_bars=reading_bars,
        actions_for=lambda symbols, first, last: [],
        sessions_between=_sessions_between,
    )


@pytest.fixture(scope="module")
def result():
    calls: list = []
    progress: list[str] = []
    report = run_signal_screen(
        _source(calls=calls), library=LIBRARY, settings=SETTINGS, progress=progress.append
    )
    return report, calls, progress


def test_bars_are_asked_for_up_to_the_last_evaluation_session_and_no_further(result):
    _, calls, progress = result
    assert [symbol for symbol, _, _ in calls] == list("ABCDEF")
    assert {(first, last) for _, first, last in calls} == {(FIRST_DAY, LAST_READ)}
    assert LAST_READ < HOLDOUT_START
    assert progress[:2] == ["bars A", "bars B"] and "5min 1/1 5m.up" in progress


def test_the_source_block_says_what_was_read(result):
    report, _, _ = result
    observed = _sessions_between(SELECTION_FIRST, LAST_READ)
    source = report["source"]
    assert source["dataset_version"] == "raw_bars_test"
    assert source["universe_timeline"] == "universe_timeline_test"
    assert source["universe_timeline_hash"] == "abc123"
    assert source["first_bar_day"] == "2024-11-11" and source["warmup_sessions"] == 5
    assert source["first_session"] == "2024-11-18" and source["last_session"] == "2026-05-15"
    assert source["sessions"] == len(observed)
    assert source["holdout_start"] == HOLDOUT_START.isoformat()
    selection, evaluation = source["pieces"]["selection"], source["pieces"]["evaluation"]
    assert selection["first"] == "2024-11-18" and evaluation["first"] == "2025-08-18"
    assert selection["sessions"] + evaluation["sessions"] == len(observed)
    assert selection["sessions"] == len(_sessions_between(SELECTION_FIRST, date(2025, 8, 15)))
    assert source["names"] == 6 and source["names_without_bars"] == ["G"]
    # The left-out session's bars are handed over and not read.
    a_day = 5 * len(SLOTS) + len(LATE_SLOTS)
    assert source["bars_not_read"] == a_day
    assert source["bars_read"] == a_day * (len(observed) + 5)
    assert source["corporate_actions"] == {"splits": 0, "cash_dividends": 0}


def test_a_name_counts_only_while_it_is_on_the_list_in_force(result):
    report, _, _ = result
    observed = _sessions_between(SELECTION_FIRST, LAST_READ)
    daily = report["scales"]["daily"]
    assert daily["sessions"] == len(observed) and daily["names"] == 6
    # Five names on the first list, four with bars on the second. The list is asked
    # at each session's opening (the first bar of any name, not of each name and not
    # the last), so the day of the rotation still counts the first.
    first_list = [day for day in observed if day <= ROTATION.date()]
    assert daily["decisions_counted"] == 5 * len(first_list) + 4 * (len(observed) - len(first_list))
    assert report["scales"]["5min"]["sessions"] == len(observed)
    assert report["library"]["complete"] is True
    assert {cell["scale"] for cell in report["cells"]} == {"5min", "daily"}
    assert all(
        set(cell["edge_by_piece_bp"]) <= {"selection", "evaluation"} for cell in report["cells"]
    )


def test_no_return_is_measured_into_the_holdout(result):
    report, _, _ = result
    observed = len(_sessions_between(SELECTION_FIRST, LAST_READ))
    by_key = {h["key"]: h for h in report["scales"]["daily"]["horizons"]}
    # Five names a session; a return k sessions on does not exist for the last k.
    for sessions_on in (1, 5, 10):
        assert by_key[f"sessions:{sessions_on}"]["measured"] <= 5 * (observed - sessions_on) + 1
    assert by_key["sessions:1"]["measured"] > by_key["sessions:10"]["measured"]


def test_a_bar_from_the_holdout_stops_the_run():
    def leaking(symbol: str, first: date, last: date) -> pd.DataFrame:
        return _bars(symbol, first, HOLDOUT_START)  # one day too many

    with pytest.raises(SignalScreenError, match="nothing from 2026-05-18 on may be"):
        run_signal_screen(_source(read_bars=leaking), library=LIBRARY, settings=SETTINGS)


def test_a_dataset_the_layout_does_not_fit_is_refused():
    with pytest.raises(SignalScreenError, match="cannot be laid on dataset raw_bars_test"):
        run_signal_screen(_source(first_day=date(2025, 1, 6)), library=LIBRARY, settings=SETTINGS)


def test_no_bars_at_all_is_refused():
    def empty(symbol: str, first: date, last: date) -> pd.DataFrame:
        return _bars(symbol, first, last).iloc[:0]

    with pytest.raises(SignalScreenError, match="no name in force has bars"):
        run_signal_screen(_source(read_bars=empty), library=LIBRARY, settings=SETTINGS)


def test_one_scale_alone_is_a_rehearsal():
    report = run_signal_screen(_source(), library=LIBRARY, scales=("daily",), settings=SETTINGS)
    assert list(report["scales"]) == ["daily"]
    assert report["library"]["complete"] is False

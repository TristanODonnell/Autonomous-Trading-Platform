"""UniverseTimeline.symbols_between: the names a run over a span needs bars for."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)

_JAN = datetime(2025, 1, 2, 22, 0, tzinfo=UTC)
_FEB = datetime(2025, 1, 31, 22, 0, tzinfo=UTC)
_MAR = datetime(2025, 2, 28, 22, 0, tzinfo=UTC)


def _period(version_id: str, symbols: list[str], start: datetime, end: datetime | None):
    return UniverseTimelinePeriod(
        universe_version_id=version_id,
        effective_from=start,
        effective_to=end,
        members=[UniverseTimelineMember(symbol=symbol) for symbol in symbols],
    )


def _timeline() -> UniverseTimeline:
    return UniverseTimeline(
        dataset_version_id="universe_timeline_test",
        window_start=_JAN.date(),
        content_hash="unused",
        periods=[
            _period("january", ["AAA", "BBB"], _JAN, _FEB),
            _period("february", ["BBB", "CCC"], _FEB, _MAR),
            _period("march_on", ["CCC", "DDD"], _MAR, None),
        ],
    )


def _at(month: int, day: int, hour: int = 0) -> datetime:
    return datetime(2025, month, day, hour, tzinfo=UTC)


def test_a_span_inside_one_list_gives_that_list() -> None:
    assert _timeline().symbols_between(_at(1, 6), _at(1, 25)) == ["AAA", "BBB"]


def test_a_span_over_a_rotation_gives_both_lists() -> None:
    assert _timeline().symbols_between(_at(1, 20), _at(2, 15)) == ["AAA", "BBB", "CCC"]


def test_a_span_over_the_whole_timeline_gives_every_name() -> None:
    timeline = _timeline()

    assert timeline.symbols_between(_at(1, 1), _at(12, 31)) == timeline.symbols_union


def test_a_name_that_left_before_the_span_is_not_in_it() -> None:
    assert "AAA" not in _timeline().symbols_between(_at(2, 3), _at(2, 15))


def test_the_last_list_has_no_end() -> None:
    assert _timeline().symbols_between(_at(6, 1), _at(6, 30)) == ["CCC", "DDD"]


def test_a_span_before_the_first_list_gives_nothing() -> None:
    assert _timeline().symbols_between(_at(1, 1), _at(1, 2)) == []


def test_a_list_that_ended_at_the_span_start_is_not_in_it() -> None:
    """A list is in force up to, not including, its end."""
    assert _timeline().symbols_between(_FEB, _at(2, 15)) == ["BBB", "CCC"]


def test_a_list_that_begins_at_the_span_end_is_not_in_it() -> None:
    """The span runs up to, not including, its end."""
    assert _timeline().symbols_between(_at(1, 6), _FEB) == ["AAA", "BBB"]


def test_times_without_a_timezone_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _timeline().symbols_between(datetime(2025, 1, 6), _at(1, 25))
    with pytest.raises(ValueError, match="timezone-aware"):
        _timeline().symbols_between(_at(1, 6), datetime(2025, 1, 25))

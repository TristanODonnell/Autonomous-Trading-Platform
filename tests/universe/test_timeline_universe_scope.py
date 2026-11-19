"""The universe scope of a research window that runs on an exported universe timeline.

january   2025-01-02 00:00 up to 2025-01-31 22:00   AAA, BBB
february  2025-01-31 22:00 up to 2025-02-28 22:00   BBB, CCC
march_on  2025-02-28 22:00 onwards                  CCC, DDD
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UNIVERSE_TIMELINE_SOURCE,
    UniverseTimeline,
    UniverseTimelineMember,
    UniverseTimelinePeriod,
)
from autonomous_trading_platform.universe.services.survivorship_guard import (
    SurvivorshipBiasError,
    SurvivorshipGuard,
)
from autonomous_trading_platform.universe.services.timeline_universe_scope import (
    universe_scope_from_timeline,
)
from autonomous_trading_platform.universe.types import UniverseResolutionMode

_JAN = datetime(2025, 1, 2, tzinfo=UTC)
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
            _period("january", ["BBB", "AAA"], _JAN, _FEB),
            _period("february", ["BBB", "CCC"], _FEB, _MAR),
            _period("march_on", ["CCC", "DDD"], _MAR, None),
        ],
    )


def test_the_scope_is_the_list_in_force_as_the_window_begins() -> None:
    scope = universe_scope_from_timeline(_timeline(), date(2025, 2, 3), date(2025, 3, 14))

    assert scope.universe_version_id == "february"
    assert scope.member_symbols == ["BBB", "CCC"]
    assert scope.member_count == 2
    assert scope.effective_timestamp == datetime(2025, 2, 3, tzinfo=UTC)
    assert scope.mode == UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY
    assert scope.source == UNIVERSE_TIMELINE_SOURCE


def test_transitions_are_the_lists_of_the_window_and_what_changed_between_them() -> None:
    scope = universe_scope_from_timeline(_timeline(), date(2025, 1, 6), date(2025, 3, 14))

    assert [t.universe_version_id for t in scope.replay_transitions] == [
        "january",
        "february",
        "march_on",
    ]
    first, second, third = scope.replay_transitions
    # As in a replay from the universe tables, the first one adds its whole list.
    assert (first.added_symbols, first.removed_symbols, first.retained_symbols) == (
        ["AAA", "BBB"],
        [],
        [],
    )
    assert (second.added_symbols, second.removed_symbols, second.retained_symbols) == (
        ["CCC"],
        ["AAA"],
        ["BBB"],
    )
    assert (third.added_symbols, third.removed_symbols, third.retained_symbols) == (
        ["DDD"],
        ["BBB"],
        ["CCC"],
    )
    assert (second.effective_from, second.effective_to) == (_FEB, _MAR)
    assert third.effective_to is None


def test_a_list_that_ended_before_the_window_is_not_a_transition() -> None:
    scope = universe_scope_from_timeline(_timeline(), date(2025, 2, 3), date(2025, 2, 14))

    assert [t.universe_version_id for t in scope.replay_transitions] == ["february"]
    assert scope.replay_transitions[0].added_symbols == ["BBB", "CCC"]


def test_a_window_inside_one_list_still_has_its_one_transition() -> None:
    """The survivorship guard refuses a rotating scope with no transitions; a window
    with no rotation in it is not that case."""
    scope = universe_scope_from_timeline(_timeline(), date(2025, 1, 6), date(2025, 1, 24))

    assert len(scope.replay_transitions) == 1
    assert scope.replay_rotation_enabled is True


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 1, 6), date(2025, 1, 24)),
        (date(2025, 1, 6), date(2025, 3, 14)),
        (date(2025, 6, 2), date(2025, 8, 29)),
    ],
)
def test_the_scope_passes_the_survivorship_guard(start: date, end: date) -> None:
    scope = universe_scope_from_timeline(_timeline(), start, end)

    SurvivorshipGuard().validate_experiment_scope(scope, start, end)


def test_a_window_that_starts_before_the_timeline_is_refused() -> None:
    with pytest.raises(SurvivorshipBiasError, match="no list in force on 2024-12-16"):
        universe_scope_from_timeline(_timeline(), date(2024, 12, 16), date(2025, 2, 14))


def test_the_first_day_of_the_timeline_is_inside_it() -> None:
    scope = universe_scope_from_timeline(_timeline(), date(2025, 1, 2), date(2025, 1, 24))

    assert scope.universe_version_id == "january"


def test_a_timeline_with_no_lists_is_refused() -> None:
    empty = UniverseTimeline(
        dataset_version_id="universe_timeline_empty",
        window_start=_JAN.date(),
        content_hash="unused",
        periods=[],
    )

    with pytest.raises(SurvivorshipBiasError, match="no list in force"):
        universe_scope_from_timeline(empty, date(2025, 1, 6), date(2025, 1, 24))

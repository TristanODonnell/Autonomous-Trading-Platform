# autonomous_trading_platform/universe/services/timeline_universe_scope.py
"""The universe scope of a research window that runs on an exported universe timeline.

A research window normally takes its scope from the universe tables (the list active on
the window's first day). On a timeline the tables are not read: the scope is built from
the timeline itself, in the shape the survivorship checks already know for a universe
that rotates inside the window (``HISTORICAL_ROTATION_REPLAY``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    UNIVERSE_TIMELINE_SOURCE,
    UniverseTimeline,
)
from autonomous_trading_platform.universe.services.survivorship_guard import (
    SurvivorshipBiasError,
)
from autonomous_trading_platform.universe.types import (
    ExperimentUniverseScope,
    UniverseResolutionMode,
    UniverseTransition,
)


def universe_scope_from_timeline(
    timeline: UniverseTimeline, start_date: date, end_date: date
) -> ExperimentUniverseScope:
    """Scope for the window ``start_date`` to ``end_date`` (both included).

    The scope's own list is the one in force when the window's first day begins, the
    same moment the universe tables are asked at. Its transitions are the lists in
    force at some moment of the window, oldest first; as in a replay from the tables,
    the first one adds every name of its list.

    Raises SurvivorshipBiasError when no list is in force as the window begins: a
    window that starts before the timeline has no point-in-time universe.
    """
    start = datetime.combine(start_date, time.min, tzinfo=UTC)
    after_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)

    first = timeline.period_at(start)
    if first is None:
        begins = timeline.periods[0].effective_from.isoformat() if timeline.periods else "never"
        raise SurvivorshipBiasError(
            f"Universe timeline {timeline.dataset_version_id} has no list in force on "
            f"{start_date} (its first list begins {begins}). The research window must "
            "start inside the timeline."
        )

    transitions: list[UniverseTransition] = []
    previous: set[str] = set()
    for period in timeline.periods:
        ends_after_start = period.effective_to is None or period.effective_to > start
        if not (period.effective_from < after_end and ends_after_start):
            continue
        current = {member.symbol for member in period.members}
        transitions.append(
            UniverseTransition(
                effective_from=period.effective_from,
                effective_to=period.effective_to,
                universe_version_id=period.universe_version_id,
                added_symbols=sorted(current - previous),
                removed_symbols=sorted(previous - current),
                retained_symbols=sorted(current & previous),
            )
        )
        previous = current

    members = first.symbols
    return ExperimentUniverseScope(
        mode=UniverseResolutionMode.HISTORICAL_ROTATION_REPLAY,
        universe_version_id=first.universe_version_id,
        effective_timestamp=start,
        member_symbols=members,
        replay_rotation_enabled=True,
        replay_transitions=transitions,
        source=UNIVERSE_TIMELINE_SOURCE,
        member_count=len(members),
    )

"""Build a universe timeline: the monthly rotation, run for every month of a window.

The live platform re-picks its universe at the close of each month's last session. A
research window of a year or more needs the same sequence of lists for its own dates,
decided before any simulation runs, so that every strategy is tested on the lists the
platform would have held and no strategy can influence them.

This runs the scheduler's own rotation step (``run_universe_at_timestamp``) once for
the window's first day and once per month end, oldest first, and keeps each result.
Run in order, each month sees the month before, so the rotation's keep-and-add ranks
and its churn guard apply as they do live. There is no picking logic here.

What a build leaves behind is the timeline: dated ``universe_versions`` (looked up
with ``UniverseVersionRepository.get_version_effective_at``) and one rotation record
per step, stamped with the rules the step ran under. A second run with the same rules
continues where the first stopped. A database holding other universe versions in the
window is refused, because the rotation works on the one universe in force.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.platform_replay.universe_hooks import (
    ROTATION_REFUSED_PREFIX,
    run_universe_at_timestamp,
)
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    PlatformReplayContext,
    UniverseReplayResult,
)
from autonomous_trading_platform.contracts.runtime.universe_timeline import (
    TIMELINE_RECORD_STATUS,
    TIMELINE_ROTATION_REASON,
    TIMELINE_SUMMARY_KEY,
    TimelineStepKind,
    UniverseTimelineBuildResult,
    UniverseTimelineRules,
    UniverseTimelineStep,
)
from autonomous_trading_platform.runtime.clock import MarketCalendar, RealMarketCalendar
from autonomous_trading_platform.storage.sor.models.universe_rotation_records import (
    UniverseRotationRecord,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_rotation_repository import (
    UniverseRotationRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)

RunUniverseStep = Callable[..., UniverseReplayResult]
Schedule = list[tuple[datetime, TimelineStepKind]]

_FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)


class UniverseTimelineError(RuntimeError):
    """The build was refused before running any step."""


def timeline_schedule(start: date, end: date, calendar: MarketCalendar) -> Schedule:
    """When each step runs: midnight UTC of the window's first day (a pick from nothing,
    on bars up to the day before), then every month's last session before ``end``, at
    the time the live end-of-day chain opens. A month end on or after ``end`` would
    pick a list for a month outside the window."""
    if end <= start:
        raise ValueError(f"end ({end}) must be after start ({start})")
    schedule: Schedule = [(datetime.combine(start, time.min, tzinfo=UTC), "start")]
    for day in calendar.trading_days(start, end - timedelta(days=1)):
        if calendar.is_last_trading_day_of_month(day):
            schedule.append((calendar.eod_window_open(day), "month_end"))
    return schedule


def build_universe_timeline(
    *,
    session: Session,
    start: date,
    end: date,
    rules: UniverseTimelineRules,
    actor: str = "operator",
    calendar: MarketCalendar | None = None,
    run_step: RunUniverseStep | None = None,
    plan_only: bool = False,
) -> UniverseTimelineBuildResult:
    """Run every step of the window's schedule that has no record yet, oldest first.

    Commits after each step, so a stopped build keeps the months it finished. Stops at
    the first step that fails; a step the rotation's safety check refuses is recorded
    and the build goes on with the list in force, as the live platform would.

    Raises UniverseTimelineError when the database holds universe versions in the
    window that this build did not make, or steps recorded under other rules.
    """
    schedule = timeline_schedule(start, end, calendar or RealMarketCalendar())
    if plan_only:
        return UniverseTimelineBuildResult(
            start=start,
            end=end,
            rules=rules,
            status="planned",
            steps=[
                UniverseTimelineStep(timestamp=ts, kind=kind, outcome="planned")
                for ts, kind in schedule
            ],
        )

    version_repo = UniverseVersionRepository(session)
    rotation_repo = UniverseRotationRepository(session)
    stamp = _stamp(rules, start)

    recorded = _recorded_steps(rotation_repo, schedule, stamp)
    _refuse_foreign_versions(version_repo, schedule, recorded)

    steps: list[UniverseTimelineStep] = []
    errors: list[str] = []
    for ts, kind in schedule:
        record = recorded.get(ts)
        if record is not None:
            steps.append(_step_from_record(version_repo, ts, kind, record))
            continue
        step, error = _run_one_step(
            session=session,
            version_repo=version_repo,
            rotation_repo=rotation_repo,
            run_step=run_step or run_universe_at_timestamp,
            timestamp=ts,
            kind=kind,
            rules=rules,
            stamp=stamp,
            actor=actor,
        )
        steps.append(step)
        if error is not None:
            errors.append(f"{ts.isoformat()}: {error}")
            break

    return UniverseTimelineBuildResult(
        start=start,
        end=end,
        rules=rules,
        status="stopped" if errors else "complete",
        steps=steps,
        symbols_union=sorted({symbol for step in steps for symbol in step.symbols}),
        errors=errors,
    )


def _stamp(rules: UniverseTimelineRules, start: date) -> dict[str, Any]:
    """What each step's rotation record carries. A rule that is off is left out, so a
    rule added later does not make an older timeline look different."""
    return {"window_start": start.isoformat(), **rules.model_dump(exclude_none=True)}


def _recorded_steps(
    rotation_repo: UniverseRotationRepository, schedule: Schedule, stamp: dict[str, Any]
) -> dict[datetime, UniverseRotationRecord]:
    """Every step a build has stored, keyed by its time. They must carry this build's
    rules and first day, sit on its schedule, and be the schedule's first steps.

    One database holds one timeline. A month's list depends on the month before it
    (names are kept until they fall far enough), so a stored month is only the right
    answer for a build with the same first day and rules; a later window inside a
    stored timeline is read from it, not built again.
    """
    recorded: dict[datetime, UniverseRotationRecord] = {}
    for row in rotation_repo.get_for_reason(TIMELINE_ROTATION_REASON):
        found = (row.summary_json or {}).get(TIMELINE_SUMMARY_KEY)
        if found != stamp:
            raise UniverseTimelineError(
                f"this database holds a timeline built with {found}; asked for {stamp}. "
                "Stored months are reused only under the first day and rules they were "
                "picked with: extend that timeline with its own --start and options, or "
                "build in a database that holds none"
            )
        if row.created_at in recorded:
            raise UniverseTimelineError(
                f"two timeline records for {row.created_at.isoformat()}; expected one"
            )
        recorded[row.created_at] = row

    last = schedule[-1][0]
    off_schedule = sorted(set(recorded) - {ts for ts, _ in schedule})
    if off_schedule and off_schedule[0] <= last:
        raise UniverseTimelineError(
            f"a stored step at {off_schedule[0].isoformat()} is not on this window's "
            "schedule; the market calendar or the step times have changed since it was built"
        )

    missing_before: datetime | None = None
    for ts, _ in schedule:
        if ts not in recorded:
            missing_before = missing_before or ts
        elif missing_before is not None:
            raise UniverseTimelineError(
                f"the step for {missing_before.isoformat()} has no record but the later "
                f"step for {ts.isoformat()} has; steps must be built oldest first"
            )
    return recorded


def _refuse_foreign_versions(
    version_repo: UniverseVersionRepository,
    schedule: Schedule,
    recorded: dict[datetime, UniverseRotationRecord],
) -> None:
    """A rotation retires "the universe in force" and activates its own, so any other
    version in force inside the window would be retired or picked up by mistake."""
    own = {record.new_universe_version_id for record in recorded.values()}
    foreign = [
        version
        for version in version_repo.get_active_versions_in_window(schedule[0][0], _FAR_FUTURE)
        if version.universe_version_id not in own
    ]
    if foreign:
        listed = ", ".join(
            f"{v.universe_version_id} (from {v.effective_from.isoformat()})" for v in foreign[:5]
        )
        raise UniverseTimelineError(
            f"{len(foreign)} universe version(s) in force on or after "
            f"{schedule[0][0].isoformat()} were not made by this timeline: {listed}. "
            "Build a timeline in a database that holds no other universe for the window"
        )


def _step_from_record(
    version_repo: UniverseVersionRepository,
    timestamp: datetime,
    kind: TimelineStepKind,
    record: UniverseRotationRecord,
) -> UniverseTimelineStep:
    outcome = TIMELINE_RECORD_STATUS.get(record.status)
    if outcome is None:
        raise UniverseTimelineError(
            f"timeline record {record.rotation_id} has status {record.status!r}"
        )
    rotated = outcome == "rotated"
    # The step's candidate keeps what the scoring saw.
    candidate = (
        version_repo.get_by_version_id(record.candidate_universe_version_id)
        if record.candidate_universe_version_id
        else None
    )
    scored = (candidate.generation_metadata_json or {}) if candidate is not None else {}
    return UniverseTimelineStep(
        timestamp=timestamp,
        kind=kind,
        outcome=outcome,
        universe_version_id=record.new_universe_version_id,
        symbols=sorted(version_repo.get_included_symbols(record.new_universe_version_id)),
        added=sorted(record.added_symbols_json or []) if rotated else [],
        removed=sorted(record.removed_symbols_json or []) if rotated else [],
        churn_pct=record.churn_pct,
        eligible_count=scored.get("included_count"),
        held_below_entry_coverage=sorted(
            (scored.get("filter_config") or {}).get("held_below_entry_coverage") or []
        ),
        reused=True,
        detail=record.rejection_reason if outcome == "refused" else None,
    )


def _run_one_step(
    *,
    session: Session,
    version_repo: UniverseVersionRepository,
    rotation_repo: UniverseRotationRepository,
    run_step: RunUniverseStep,
    timestamp: datetime,
    kind: TimelineStepKind,
    rules: UniverseTimelineRules,
    stamp: dict[str, Any],
    actor: str,
) -> tuple[UniverseTimelineStep, str | None]:
    """One rotation, kept. Returns the step and, when the build must stop, why."""
    result = run_step(
        session=session,
        timestamp=timestamp,
        replay_context=PlatformReplayContext.create(symbols=[], timestamp=timestamp, actor=actor),
        # As the scheduler's month-end step: the churn guard stays on.
        force_rotation=False,
        rotation_reason=TIMELINE_ROTATION_REASON,
        screener_source=rules.screener_source,
        candidate_market_data=rules.candidate_market_data,
        min_intraday_bar_coverage=rules.min_intraday_bar_coverage,
        held_min_intraday_bar_coverage=rules.held_min_intraday_bar_coverage,
    )
    eligible = result.summary.get("candidate_included")
    held_below_entry = sorted(result.summary.get("held_below_entry_coverage") or [])

    if result.status == "ok" and result.universe_version_id is not None:
        session.flush()
        record = rotation_repo.get_by_rotation_id(str(result.summary.get("rotation_id")))
        if record is None:
            session.rollback()
            return _failed(timestamp, kind, "the rotation left no record to stamp")
        # A new dict, so the change to the JSON column is seen and written.
        record.summary_json = {**(record.summary_json or {}), TIMELINE_SUMMARY_KEY: stamp}
        session.commit()
        return (
            UniverseTimelineStep(
                timestamp=timestamp,
                kind=kind,
                outcome="rotated" if result.rotation_applied else "unchanged",
                universe_version_id=result.universe_version_id,
                symbols=sorted(version_repo.get_included_symbols(result.universe_version_id)),
                added=list(result.summary.get("added") or []) if result.rotation_applied else [],
                removed=(
                    list(result.summary.get("removed") or []) if result.rotation_applied else []
                ),
                churn_pct=result.churn_pct,
                eligible_count=eligible,
                held_below_entry_coverage=held_below_entry,
            ),
            None,
        )

    session.rollback()
    reason = result.errors[0] if result.errors else f"the universe step returned {result.status}"
    if result.status != "failed" or not reason.startswith(ROTATION_REFUSED_PREFIX):
        return _failed(timestamp, kind, reason)

    in_force = version_repo.get_version_effective_at(timestamp)
    if in_force is None:
        return _failed(timestamp, kind, f"refused with no universe in force: {reason}")
    # The rotation raised before writing anything, so the refusal is recorded here:
    # every step of a timeline leaves one record.
    rotation_repo.insert_record(
        UniverseRotationRecord(
            rotation_id=str(uuid.uuid4()),
            rotation_type="scheduled",
            previous_universe_version_id=in_force.universe_version_id,
            candidate_universe_version_id=result.summary.get("candidate_version_id"),
            new_universe_version_id=in_force.universe_version_id,
            force_rotation=False,
            rotation_reason=TIMELINE_ROTATION_REASON,
            approved_by=actor,
            approval_required=False,
            created_at=timestamp,
            status="rejected",
            rejection_reason=reason,
            summary_json={TIMELINE_SUMMARY_KEY: stamp},
        )
    )
    session.commit()
    return (
        UniverseTimelineStep(
            timestamp=timestamp,
            kind=kind,
            outcome="refused",
            universe_version_id=in_force.universe_version_id,
            symbols=sorted(version_repo.get_included_symbols(in_force.universe_version_id)),
            eligible_count=eligible,
            held_below_entry_coverage=held_below_entry,
            detail=reason,
        ),
        None,
    )


def _failed(
    timestamp: datetime, kind: TimelineStepKind, reason: str
) -> tuple[UniverseTimelineStep, str]:
    return (
        UniverseTimelineStep(timestamp=timestamp, kind=kind, outcome="failed", detail=reason),
        reason,
    )

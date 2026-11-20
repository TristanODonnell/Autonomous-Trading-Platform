# autonomous_trading_platform/contracts/runtime/universe_timeline.py
"""A universe timeline: one universe per month over a window, built before any simulation.

The timeline is not a table of its own. It is the dated ``universe_versions`` a build
leaves behind, plus one rotation record per step carrying the rules the step ran under.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autonomous_trading_platform.contracts.common.types import UTCDateTime

# ``rotation_reason`` on every rotation record a build writes.
TIMELINE_ROTATION_REASON = "timeline_build"
# Key under a rotation record's ``summary_json`` holding the step's rules.
TIMELINE_SUMMARY_KEY = "timeline"

TimelineStepKind = Literal["start", "month_end"]
# rotated: a new list is in force. unchanged: the proposal equalled the list in force.
# refused: the rotation's safety check turned the proposal down (the churn guard, for
# one), so the list in force stays, as it would on the live platform. failed: the pool
# refresh, the scoring or the rotation broke; the build stops there. planned: not run.
TimelineStepOutcome = Literal["rotated", "unchanged", "refused", "failed", "planned"]
TimelineBuildStatus = Literal["planned", "complete", "stopped"]

# Rotation record ``status`` for each outcome a build stores.
TIMELINE_RECORD_STATUS: dict[str, TimelineStepOutcome] = {
    "completed": "rotated",
    "skipped": "unchanged",
    "rejected": "refused",
}


class UniverseTimelineRules(BaseModel):
    """What a stored month must have been picked under to be reused."""

    model_config = ConfigDict(frozen=True)

    screener_source: str
    candidate_market_data: str
    min_intraday_bar_coverage: float | None = None
    held_min_intraday_bar_coverage: float | None = None


class UniverseTimelineStep(BaseModel):
    timestamp: UTCDateTime
    kind: TimelineStepKind
    outcome: TimelineStepOutcome
    universe_version_id: str | None = None
    symbols: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    churn_pct: float | None = None
    eligible_count: int | None = None
    # Held names kept only by the lower coverage line for held names.
    held_below_entry_coverage: list[str] = Field(default_factory=list)
    # True when an earlier build under the same rules already covered the step.
    reused: bool = False
    detail: str | None = None


class UniverseTimelineBuildResult(BaseModel):
    start: date
    end: date
    rules: UniverseTimelineRules
    status: TimelineBuildStatus
    steps: list[UniverseTimelineStep] = Field(default_factory=list)
    # Every name that is a member in some month: the list to backfill bars for.
    symbols_union: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# The timeline as a dataset (``atp universe export-timeline``)
# ---------------------------------------------------------------------------

# ``metadata_json.dataset_lifecycle`` of an exported timeline's ``dataset_versions`` row.
UNIVERSE_TIMELINE_LIFECYCLE = "universe_timeline"
# ``source`` of that row, and ``source_manifest.pipeline``.
UNIVERSE_TIMELINE_SOURCE = "universe_timeline_export"

# created: a new version was written. up_to_date: the newest version already holds this
# timeline, nothing written. planned: what would be written. refused: the stored
# timeline does not hold together (see ``errors``), nothing written.
TimelineExportStatus = Literal["created", "up_to_date", "planned", "refused"]


class UniverseTimelineMember(BaseModel):
    symbol: str
    # Rank in the candidate pool the list was picked from, not a position from 1 to 20.
    rank: int | None = None


class UniverseTimelinePeriod(BaseModel):
    """One list and the time it was in force: from ``effective_from`` up to, not
    including, ``effective_to``. The last list of a timeline has no ``effective_to``."""

    universe_version_id: str
    effective_from: UTCDateTime
    effective_to: UTCDateTime | None = None
    members: list[UniverseTimelineMember] = Field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return sorted(member.symbol for member in self.members)


class UniverseTimelineExportStep(BaseModel):
    """One step of the build, as its rotation record tells it."""

    timestamp: UTCDateTime
    outcome: TimelineStepOutcome
    # The list in force after the step: a new one when rotated, the same one otherwise.
    universe_version_id: str


class UniverseTimelineExportResult(BaseModel):
    status: TimelineExportStatus
    dataset_version_id: str | None = None
    window_start: date | None = None
    # The rules every step ran under, as stamped on its rotation record.
    rules: dict[str, Any] = Field(default_factory=dict)
    first_step_at: UTCDateTime | None = None
    last_step_at: UTCDateTime | None = None
    steps: list[UniverseTimelineExportStep] = Field(default_factory=list)
    periods: list[UniverseTimelinePeriod] = Field(default_factory=list)
    symbols_union: list[str] = Field(default_factory=list)
    rows: int = 0
    # Over the rules, the steps and the lists with their dates; no database ids. Two
    # databases holding the same timeline give the same value.
    content_hash: str | None = None
    errors: list[str] = Field(default_factory=list)


class UniverseTimeline(BaseModel):
    """An exported timeline read back by its dataset version id: which list was in
    force at any moment of the window."""

    model_config = ConfigDict(frozen=True)

    dataset_version_id: str
    window_start: date
    rules: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    steps: list[UniverseTimelineExportStep] = Field(default_factory=list)
    # Oldest first, each ending where the next begins; the last one is open.
    periods: list[UniverseTimelinePeriod] = Field(default_factory=list)

    @property
    def symbols_union(self) -> list[str]:
        return sorted({m.symbol for period in self.periods for m in period.members})

    def period_at(self, moment: datetime) -> UniverseTimelinePeriod | None:
        """The list in force at ``moment``; None before the timeline's first step.

        A list is in force from its start up to, not including, its end, so the moment
        of a rotation belongs to the new list.
        """
        if moment.tzinfo is None:
            raise ValueError("a timeline is asked with a timezone-aware time")
        for period in reversed(self.periods):
            if period.effective_from <= moment:
                if period.effective_to is None or moment < period.effective_to:
                    return period
                return None
        return None

    def members_at(self, moment: datetime) -> frozenset[str]:
        """The names in the list in force at ``moment``; none before the first step."""
        period = self.period_at(moment)
        return frozenset(m.symbol for m in period.members) if period is not None else frozenset()

    def symbols_between(self, start: datetime, end: datetime) -> list[str]:
        """Every name of a list in force at some moment from ``start`` up to, not
        including, ``end``: the names a run over that span needs bars for."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("a timeline is asked with timezone-aware times")
        names: set[str] = set()
        for period in self.periods:
            begins_before_end = period.effective_from < end
            ends_after_start = period.effective_to is None or period.effective_to > start
            if begins_before_end and ends_after_start:
                names.update(member.symbol for member in period.members)
        return sorted(names)

    def symbols_on_dates(self, first: date, last: date) -> list[str]:
        """Every name of a list in force at some moment of the days ``first`` to
        ``last``, both included, counted as whole UTC days. A window and the runs
        inside it use this one rule, so a run never needs a name its window lacks."""
        return self.symbols_between(
            datetime.combine(first, time.min, tzinfo=UTC),
            datetime.combine(last + timedelta(days=1), time.min, tzinfo=UTC),
        )

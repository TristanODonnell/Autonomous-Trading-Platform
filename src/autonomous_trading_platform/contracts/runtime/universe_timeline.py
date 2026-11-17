# autonomous_trading_platform/contracts/runtime/universe_timeline.py
"""A universe timeline: one universe per month over a window, built before any simulation.

The timeline is not a table of its own. It is the dated ``universe_versions`` a build
leaves behind, plus one rotation record per step carrying the rules the step ran under.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

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

"""Shapes for a research-window dataset: one raw-bars version over a long window.

The long research window is fetched as monthly backfill versions. ``atp datasets
build-window`` merges them into one version and reports what it found in the bars.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RESEARCH_WINDOW_LIFECYCLE = "research_window"
RESEARCH_WINDOW_SOURCE = "research_window_raw_bars"

WindowDatasetStatus = Literal["created", "up_to_date", "planned", "refused"]


class SessionShape(BaseModel):
    """A session whose number of distinct bar times, over every symbol, is not the
    number its opening hours give."""

    session: date
    bar_times: int
    expected: int


class PriceJump(BaseModel):
    """A close that differs from the same symbol's previous close by more than the
    reporting threshold. A split in raw prices shows up here."""

    symbol: str
    timestamp: datetime
    previous_close: float
    close: float
    change: float


class ResearchWindowDatasetResult(BaseModel):
    status: WindowDatasetStatus
    start: date
    end: date
    symbols: list[str]
    dataset_version_id: str | None = None
    source_versions: list[str] = Field(default_factory=list)
    rows: int = 0
    # Sessions in the window, less the ones left out by name.
    sessions_expected: int = 0
    # Sessions left out on the operator's word because the feed has no usable data.
    sessions_excluded: list[date] = Field(default_factory=list)
    # Dataset-wide faults: either one refuses the build.
    sessions_missing: list[date] = Field(default_factory=list)
    sessions_off_shape: list[SessionShape] = Field(default_factory=list)
    # Per-symbol facts: reported, never a refusal.
    symbol_coverage: dict[str, float] = Field(default_factory=dict)
    symbol_sessions_missing: dict[str, int] = Field(default_factory=dict)
    price_jumps: list[PriceJump] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

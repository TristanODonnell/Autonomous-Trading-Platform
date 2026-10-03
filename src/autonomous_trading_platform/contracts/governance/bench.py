# autonomous_trading_platform/contracts/governance/bench.py
"""
Bench management (portfolio rotation step 3).

A bench review re-simulates every tracked strategy on a recent window, groups
near-duplicates by return correlation, keeps one champion per group, admits new
research output only when it is novel or better, and retires stale, weak or
redundant candidates so the bench stays small, diverse and current.
"""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.types import UTCDateTime

# Tier label for candidates that have never been reviewed.
PENDING_TIER = "pending"


class BenchDecision(enum.StrEnum):
    # Pending candidate admitted to the bench.
    ADMIT = "admit"
    # Bench member stays on the bench.
    KEEP = "keep"
    # Candidate retired (governance `retired`): redundant, weak, stale or over the cap.
    RETIRE = "retire"
    # ACTIVE / ON_DECK member: re-simulated and grouped, never pruned by the bench review.
    PROTECTED = "protected"
    # Could not be re-simulated this review (no config, no data); nothing changes.
    SKIPPED = "skipped"


class BenchEvaluation(BaseModel):
    review_id: str
    strategy_id: str
    reviewed_at: UTCDateTime
    tier: str
    strategy_type: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    resim_run_id: UUID | None = None
    trade_count: int | None = None
    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    score: Decimal | None = None
    group_id: str | None = None
    is_champion: bool = False
    max_correlation: float | None = None
    correlated_with: str | None = None
    floor_strikes: int = 0
    decision: BenchDecision
    reason: str


class BenchReviewResult(BaseModel):
    review_id: str
    reviewed_at: UTCDateTime
    window_start: date | None = None
    window_end: date | None = None
    evaluations: list[BenchEvaluation]
    admitted: list[str] = []
    retired: list[str] = []
    bench: list[str] = []
    group_count: int = 0

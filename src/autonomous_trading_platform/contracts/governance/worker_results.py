"""What the research worker (Box B) hands to Box A, and what A decides about it.

Pre-launch plan 3.1/3.2. A result carries enough lineage for A to refuse it: which
datasets it was built on (``dataset_as_of`` is the session whose publish index the worker
pinned), which code produced it (``git_sha``) and which run (``run_id``, idempotency key).

``payload`` is typed per ``result_type``; today only ``bench_resims`` has a schema
(``BenchResimsPayload``), the shape the weekly portfolio review consumes.
"""

from __future__ import annotations

import enum
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from autonomous_trading_platform.contracts.common.types import UTCDateTime


class WorkerResultType(enum.StrEnum):
    # Weekly: bench re-sims + scorecards feeding the portfolio review.
    BENCH_RESIMS = "bench_resims"
    # Monthly: research pipeline / strategy generation candidates for the bench.
    RESEARCH_CANDIDATES = "research_candidates"
    # Monthly: regime-suite allocation / governance parameter recommendations.
    GOVERNANCE_PARAMETERS = "governance_parameters"


class WorkerResultStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    APPLIED = "applied"


class WorkerResultRejection(enum.StrEnum):
    AUTOMATION_DISABLED = "worker_results_disabled"
    KILL_SWITCH = "kill_switch_enabled"
    STALE_DATASET = "stale_dataset"
    MISSING_DATASET_AS_OF = "missing_dataset_as_of"
    GIT_SHA_MISMATCH = "git_sha_mismatch"
    MISSING_GIT_SHA = "missing_git_sha"
    EMPTY_PAYLOAD = "empty_payload"
    INVALID_PAYLOAD = "invalid_payload"


class WorkerResultSubmission(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    result_type: WorkerResultType
    produced_at: UTCDateTime
    git_sha: str | None = Field(default=None, max_length=64)
    worker_host: str | None = Field(default=None, max_length=128)
    fixture: str | None = Field(default=None, max_length=256)
    dataset_as_of: date | None = None
    dataset_versions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkerResultDecision(BaseModel):
    run_id: str
    result_type: WorkerResultType
    status: WorkerResultStatus
    reason: str | None = None
    # True when this run_id had already been decided; the stored decision is returned.
    duplicate: bool = False
    received_at: UTCDateTime
    dataset_age_days: int | None = None


class WorkerResultRecord(BaseModel):
    """A stored submission, as the API lists it (payload left out)."""

    run_id: str
    result_type: WorkerResultType
    status: WorkerResultStatus
    rejection_reason: str | None = None
    worker_host: str | None = None
    git_sha: str | None = None
    fixture: str | None = None
    produced_at: UTCDateTime | None = None
    dataset_as_of: date | None = None
    dataset_versions: list[str] = Field(default_factory=list)
    received_at: UTCDateTime
    applied_at: UTCDateTime | None = None
    review_id: str | None = None


# ---------------------------------------------------------------- bench_resims payload


class ResimOutcomePayload(BaseModel):
    """One strategy's re-simulation over the bench window (wire form of ``ResimOutcome``)."""

    strategy_id: str = Field(min_length=1, max_length=128)
    strategy_type: str | None = None
    run_id: str | None = None
    trade_count: int | None = None
    total_return: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    score: Decimal | None = None
    daily_turnover: float | None = None
    # ISO date → close-to-close return for that day.
    daily_returns: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class BenchResimsPayload(BaseModel):
    window_start: date
    window_end: date
    dataset_version: str | None = None
    outcomes: list[ResimOutcomePayload] = Field(min_length=1)
    # ISO date → market (benchmark) daily return, for the review's correlation lens.
    market_returns: dict[str, float] = Field(default_factory=dict)


# ---------------------------------------------------------------- bench re-sim inputs


class BenchResimStrategyInput(BaseModel):
    """One strategy the worker must re-simulate: its tier on A and its stored config."""

    strategy_id: str = Field(min_length=1, max_length=128)
    tier: str = Field(min_length=1, max_length=32)
    strategy_type: str | None = None
    config_hash: str | None = None
    # The research parameters as ``stored_config_parameters`` unwraps them.
    parameters: dict[str, Any] = Field(default_factory=dict)


class BenchResimInputs(BaseModel):
    """What Box A hands the worker for the weekly bench re-sims (plan 3.3a).

    The worker runs every listed strategy over the shared window on the named dataset
    version it pulled from A, and posts the outcomes back as ``BenchResimsPayload``.
    ``window_*`` are None when A cannot build a window yet (no validated dataset, or
    too little history); ``window_reason`` says why.
    """

    as_of: date
    git_sha: str | None = None
    trading_days: int
    window_start: date | None = None
    window_end: date | None = None
    window_reason: str | None = None
    dataset_version: str | None = None
    price_basis: str | None = None
    symbols: list[str] = Field(default_factory=list)
    # Same sizing the review's own re-sims use, so the worker's numbers are comparable.
    initial_cash: float = 100_000.0
    random_seed: int = 42
    strategies: list[BenchResimStrategyInput] = Field(default_factory=list)

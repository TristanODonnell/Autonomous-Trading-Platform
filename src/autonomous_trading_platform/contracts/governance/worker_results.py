"""What the research worker (Box B) hands to Box A, and what A decides about it.

Pre-launch plan 3.1/3.2. A result carries enough lineage for A to refuse it: which
datasets it was built on (``dataset_as_of`` is the session whose publish index the worker
pinned), which code produced it (``git_sha``) and which run (``run_id``, idempotency key).
"""

from __future__ import annotations

import enum
from datetime import date
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

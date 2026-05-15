from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from autonomous_trading_platform.contracts.common.types import UTCDateTime


@dataclass(frozen=True)
class RuntimeJobRunStep:
    step_id: UUID
    job_run_id: str
    step_name: str
    status: str
    sequence_number: int
    started_at: UTCDateTime
    completed_at: UTCDateTime | None
    duration_ms: int | None
    error_message: str | None
    error_type: str | None

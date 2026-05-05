from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autonomous_trading_platform.contracts.common.types import UTCDateTime


@dataclass(frozen=True)
class RuntimeJobRun:
    job_run_id: str
    job_name: str
    parent_job_run_id: str | None
    status: str
    trigger_type: str
    started_at: UTCDateTime
    completed_at: UTCDateTime | None
    duration_ms: int | None
    error_message: str | None
    correlation_id: str | None
    input_summary_json: dict[str, Any] | None
    output_summary_json: dict[str, Any] | None

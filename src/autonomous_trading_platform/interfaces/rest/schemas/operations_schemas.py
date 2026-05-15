from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class OperationsJobSummaryResponse(BaseModel):
    job_name: str
    latest_status: str
    last_started_at: datetime
    last_completed_at: datetime | None
    last_duration_ms: int | None
    last_error_message: str | None
    run_count: int


class OperationsJobsResponse(BaseModel):
    jobs: list[OperationsJobSummaryResponse]


class OperationsJobRunResponse(BaseModel):
    job_run_id: str
    job_name: str
    parent_job_run_id: str | None
    status: str
    trigger_type: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    error_message: str | None
    correlation_id: str | None
    tempo_link: str | None
    loki_link: str | None
    input_summary: dict[str, Any] | None
    output_summary: dict[str, Any] | None


class OperationsJobRunsResponse(BaseModel):
    job_name: str
    runs: list[OperationsJobRunResponse]


class OperationsRuntimeStateResponse(BaseModel):
    trading_enabled: bool
    trading_paused: bool
    kill_switch_enabled: bool
    trading_mode: str
    reason: str | None
    updated_by: str | None
    updated_at: datetime

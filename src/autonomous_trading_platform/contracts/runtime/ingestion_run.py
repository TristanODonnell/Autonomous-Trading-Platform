from __future__ import annotations

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.contracts.common.types import UTCDateTime


class IngestionRun(BaseModel):
    ingestion_run_id: str
    created_at: UTCDateTime
    run_timestamp: UTCDateTime
    run_type: RunType
    source: str
    dataset_version: str
    status: str
    started_at: UTCDateTime
    completed_at: UTCDateTime | None = None
    error_message: str | None = None
    row_count: int | None = None
    file_count: int | None = None

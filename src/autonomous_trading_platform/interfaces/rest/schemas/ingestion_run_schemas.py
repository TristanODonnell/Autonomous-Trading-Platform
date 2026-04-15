from datetime import datetime

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import RunType


class CreateIngestionRunRequest(BaseModel):
    ingestion_run_id: str
    run_timestamp: datetime
    run_type: RunType
    source: str
    dataset_version: str
    status: str


class FailIngestionRunRequest(BaseModel):
    error_message: str


class MetadataActionResponse(BaseModel):
    message: str

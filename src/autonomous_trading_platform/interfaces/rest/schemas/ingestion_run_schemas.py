from datetime import date, datetime

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


class LatestDatasetVersionResponse(BaseModel):
    dataset_version_id: str
    dataset_name: str
    created_at: datetime
    source: str
    price_basis: str
    interval: str
    schema_version: str
    symbol_coverage: int | None
    date_coverage_start: date | None
    date_coverage_end: date | None
    validation_status: str


class LatestFeatureVersionResponse(BaseModel):
    dataset_version_id: str
    feature_name: str
    dataset_name: str
    created_at: datetime
    schema_version: str
    source_dataset_version: str
    underlying_price_basis: str
    symbol_coverage: int | None
    date_coverage_start: date | None
    date_coverage_end: date | None
    validation_status: str

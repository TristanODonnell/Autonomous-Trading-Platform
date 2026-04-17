from datetime import date, datetime

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis


class CreateDatasetVersionRequest(BaseModel):
    dataset_version_id: str
    dataset_name: str
    source: str
    price_basis: PriceBasis | None
    interval: BarInterval | None
    schema_version: str
    validation_status: str
    source_manifest: dict | None = None
    metadata_json: dict | None = None
    symbol_coverage: int | None = None
    date_coverage_start: date | None = None
    date_coverage_end: date | None = None
    checksum: str | None = None
    created_at: datetime | None = None


class MetadataActionResponse(BaseModel):
    message: str

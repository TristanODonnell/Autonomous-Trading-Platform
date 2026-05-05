from datetime import date, datetime

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import PriceBasis


class CreateFeatureDatasetVersionRequest(BaseModel):
    dataset_version_id: str | None = None
    feature_name: str
    dataset_name: str = "features"
    underlying_price_basis: PriceBasis
    underlying_dataset_version: str
    schema_version: str
    validation_status: str
    computation_parameters: dict
    storage_path: str
    source_manifest: dict | None = None
    metadata_json: dict | None = None
    symbol_coverage: int | None = None
    date_coverage_start: date | None = None
    date_coverage_end: date | None = None
    checksum: str | None = None
    created_at: datetime | None = None
    computation_code_version: str

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.common.types import UTCDateTime


@dataclass(frozen=True)
class FeatureDatasetVersion:
    dataset_version_id: str
    feature_name: str
    dataset_name: str
    created_at: UTCDateTime
    schema_version: str
    underlying_dataset_version: str
    underlying_price_basis: PriceBasis
    computation_parameters: dict[str, Any] | None
    storage_path: str
    symbol_coverage: int | None
    date_coverage_start: date | None
    date_coverage_end: date | None
    validation_status: str
    checksum: str | None
    source_manifest: dict[str, Any] | None
    metadata_json: dict[str, Any] | None

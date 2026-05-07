from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.contracts.common.types import UTCDateTime


class DatasetVersion(BaseModel):
    dataset_version_id: str
    dataset_name: str
    created_at: UTCDateTime
    source: str
    price_basis: PriceBasis
    interval: BarInterval
    schema_version: str
    symbol_coverage: int | None = None
    date_coverage_start: date | None = None
    date_coverage_end: date | None = None
    validation_status: str
    checksum: str | None = None
    source_manifest: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    source_dataset_version: str | None = None

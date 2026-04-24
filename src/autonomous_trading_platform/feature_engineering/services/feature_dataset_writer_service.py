from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, cast

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.storage.parquet.versioning import generate_dataset_version


class FeatureDatasetWriterService:
    """
    Writes computed feature datasets and registers their metadata.
    """

    def __init__(
        self,
        feature_dataset_repository: Any,
        parquet_writer: Any,
    ) -> None:
        self._feature_dataset_repository = feature_dataset_repository
        self._parquet_writer = parquet_writer

    def write_feature_dataset(
        self,
        *,
        feature_name: str,
        source_dataset_version_id: str,
        source_price_basis: PriceBasis,
        frame: pd.DataFrame,
        computation_parameters: dict[str, object],
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> FeatureDatasetVersion:
        if frame.empty:
            raise ValueError(f"Cannot write empty feature dataset for feature={feature_name}.")

        feature_dataset_version_id = generate_dataset_version(feature_name)

        self._parquet_writer.write_feature_frame(
            frame=frame,
            feature_name=feature_name,
            dataset_version_id=feature_dataset_version_id,
        )

        contract = FeatureDatasetVersion(
            dataset_version_id=feature_dataset_version_id,
            feature_name=feature_name,
            dataset_name=f"{feature_name}_features",
            created_at=datetime.now(UTC),
            schema_version="feature_schema_v1",
            source_dataset_version=source_dataset_version_id,
            underlying_price_basis=source_price_basis,
            computation_parameters=computation_parameters,
            storage_path=f"feature_datasets/{feature_name}/dataset_version={feature_dataset_version_id}/",
            symbol_coverage=len(frame["symbol"].dropna().unique())
            if "symbol" in frame.columns
            else None,
            date_coverage_start=start_date,
            date_coverage_end=end_date,
            validation_status="unvalidated",
            metadata_json={
                "symbols": symbols,
                "row_count": len(frame),
            },
            checksum=None,
            source_manifest={
                "feature_name": feature_name,
                "source_dataset_version_id": source_dataset_version_id,
                "computation_parameters": computation_parameters,
            },
            computation_code_version="v1",
        )

        saved = self._feature_dataset_repository.save(contract)
        return cast(FeatureDatasetVersion, saved)

    def mark_validated(
        self,
        *,
        feature_dataset_version: FeatureDatasetVersion,
    ) -> FeatureDatasetVersion:
        updated = replace(
            feature_dataset_version,
            validation_status="validated",
        )
        saved = self._feature_dataset_repository.save(updated)
        return cast(FeatureDatasetVersion, saved)

    def mark_failed(
        self,
        *,
        feature_dataset_version: FeatureDatasetVersion,
    ) -> FeatureDatasetVersion:
        updated = replace(
            feature_dataset_version,
            validation_status="failed",
        )
        saved = self._feature_dataset_repository.save(updated)
        return cast(FeatureDatasetVersion, saved)

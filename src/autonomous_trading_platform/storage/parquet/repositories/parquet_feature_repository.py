from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.storage.parquet.datasets import (
    FEATURE_LIQUIDITY_DATASET,
    FEATURE_MOVING_AVERAGE_DATASET,
    FEATURE_REGIME_DATASET,
    FEATURE_RETURNS_DATASET,
    FEATURE_VOLATILITY_DATASET,
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.metadata import (
    attach_metadata,
    build_feature_metadata,
)

FEATURE_DATASETS_BY_NAME = {
    "returns": FEATURE_RETURNS_DATASET,
    "volatility": FEATURE_VOLATILITY_DATASET,
    "moving_average": FEATURE_MOVING_AVERAGE_DATASET,
    "liquidity": FEATURE_LIQUIDITY_DATASET,
    "regime": FEATURE_REGIME_DATASET,
}


class ParquetFeatureRepository:
    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def _dataset_root(self, dataset: ParquetDataset, dataset_version: str) -> Path:
        root = self.base_path
        for part in dataset.root_parts:
            root = root / part
        return root / f"dataset_version={dataset_version}"

    def _validate_table(self, table: pa.Table, *, dataset: ParquetDataset) -> None:
        expected_schema = dataset.schema

        if table.schema.names != expected_schema.names:
            raise ValueError(
                f"Feature table columns do not match expected schema. "
                f"Expected={expected_schema.names}, actual={table.schema.names}"
            )

        for expected_field, actual_field in zip(
            expected_schema,
            table.schema,
            strict=True,
        ):
            if expected_field.type != actual_field.type:
                raise ValueError(
                    f"Feature table field type mismatch for '{expected_field.name}': "
                    f"expected={expected_field.type}, actual={actual_field.type}"
                )

    def _prepare_feature_frame_for_write(
        self,
        *,
        frame: pd.DataFrame,
        dataset: ParquetDataset,
        source_dataset_version_id: str,
        source_price_basis: PriceBasis,
    ) -> pd.DataFrame:
        prepared = frame.copy()

        if "timestamp" not in prepared.columns:
            raise ValueError("Feature frame is missing required column: timestamp")

        timestamp = pd.to_datetime(prepared["timestamp"], utc=True)

        if "date" not in prepared.columns:
            prepared["date"] = timestamp.dt.date

        if "year" not in prepared.columns:
            prepared["year"] = timestamp.dt.strftime("%Y")

        if "month" not in prepared.columns:
            prepared["month"] = timestamp.dt.strftime("%m")

        if "underlying_dataset_version" not in prepared.columns:
            prepared["underlying_dataset_version"] = source_dataset_version_id

        if "price_basis" not in prepared.columns:
            prepared["price_basis"] = source_price_basis.value

        missing_columns = [
            column for column in dataset.schema.names if column not in prepared.columns
        ]
        if missing_columns:
            raise ValueError(
                f"Feature frame is missing required schema columns: {missing_columns}. "
                f"feature columns={list(prepared.columns)}, "
                f"expected columns={dataset.schema.names}"
            )

        return prepared[dataset.schema.names]

    def write_feature_frame(
        self,
        *,
        frame: pd.DataFrame,
        feature_name: str,
        dataset_version_id: str,
        source_dataset_version_id: str,
        source_price_basis: PriceBasis,
    ) -> None:
        try:
            dataset = FEATURE_DATASETS_BY_NAME[feature_name]
        except KeyError as exc:
            raise ValueError(f"Unsupported feature_name={feature_name}") from exc

        prepared_frame = self._prepare_feature_frame_for_write(
            frame=frame,
            dataset=dataset,
            source_dataset_version_id=source_dataset_version_id,
            source_price_basis=source_price_basis,
        )

        table = pa.Table.from_pandas(prepared_frame, preserve_index=False)

        self.write_feature_dataset(
            dataset=dataset,
            dataset_version=dataset_version_id,
            table=table,
            creation_timestamp=datetime.now(UTC).isoformat(),
            checksum="",
            feature_name=feature_name,
            underlying_dataset_version=source_dataset_version_id,
            underlying_price_basis=source_price_basis,
        )

    def write_feature_dataset(
        self,
        *,
        dataset: ParquetDataset,
        dataset_version: str,
        table: pa.Table,
        creation_timestamp: str,
        checksum: str,
        feature_name: str,
        underlying_dataset_version: str,
        underlying_price_basis: PriceBasis,
    ) -> None:
        table = table.cast(dataset.schema)
        self._validate_table(table, dataset=dataset)

        feature_metadata = build_feature_metadata(
            dataset=dataset,
            dataset_version=dataset_version,
            creation_timestamp=creation_timestamp,
            checksum=checksum,
            feature_name=feature_name,
            underlying_dataset_version=underlying_dataset_version,
            underlying_price_basis=underlying_price_basis.value,
        )

        schema_with_metadata = attach_metadata(dataset.schema, feature_metadata)

        table = table.replace_schema_metadata(schema_with_metadata.metadata)

        dataset_root = self._dataset_root(dataset, dataset_version)
        dataset_root.mkdir(parents=True, exist_ok=True)

        ds.write_dataset(
            data=table,
            base_dir=str(dataset_root),
            format="parquet",
            partitioning=list(dataset.partition_cols),
            existing_data_behavior="overwrite_or_ignore",
        )

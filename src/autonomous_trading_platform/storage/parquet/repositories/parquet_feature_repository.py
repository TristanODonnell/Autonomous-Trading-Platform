from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from autonomous_trading_platform.storage.parquet.datasets import FEATURE_RETURNS_DATASET
from autonomous_trading_platform.storage.parquet.metadata import attach_metadata, build_metadata


class ParquetFeatureRepository:
    def __init__(self, *, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def write_returns_features(
        self,
        *,
        dataset_version: str,
        table: pa.Table,
        creation_timestamp: str,
        checksum: str,
    ) -> None:
        self._validate_table(table)

        metadata = build_metadata(
            dataset=FEATURE_RETURNS_DATASET,
            dataset_version=dataset_version,
            ingestion_timestamp=creation_timestamp,
            checksum=checksum,
        )

        schema_with_metadata = attach_metadata(
            FEATURE_RETURNS_DATASET.schema,
            metadata,
        )

        table = table.cast(FEATURE_RETURNS_DATASET.schema)
        table = table.replace_schema_metadata(schema_with_metadata.metadata)

        dataset_root = self._dataset_root(dataset_version)
        dataset_root.mkdir(parents=True, exist_ok=True)

        ds.write_dataset(
            data=table,
            base_dir=str(dataset_root),
            format="parquet",
            partitioning=list(FEATURE_RETURNS_DATASET.partition_cols),
            existing_data_behavior="overwrite_or_ignore",
        )

    def _dataset_root(self, dataset_version: str) -> Path:
        root = self.base_path
        for part in FEATURE_RETURNS_DATASET.root_parts:
            root = root / part
        return root / f"dataset_version={dataset_version}"

    def _validate_table(self, table: pa.Table) -> None:
        expected_schema = FEATURE_RETURNS_DATASET.schema

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

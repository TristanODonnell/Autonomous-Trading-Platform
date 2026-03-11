from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from .datasets import ParquetDataset
from .helpers.compute_checksum import compute_table_checksum
from .metadata import attach_metadata, build_metadata
from .paths import dataset_version_root


def write_table(
    table: pa.Table,
    dataset: ParquetDataset,
    base_path: str | Path,
    data_version: str,
) -> None:
    """
    Write a PyArrow table to a Parquet dataset with partitioning and metadata.
    """
    ingestion_timestamp = datetime.now(UTC).isoformat()

    table = table.cast(dataset.schema)

    checksum = compute_table_checksum(table)  # Build metadata

    metadata = build_metadata(
        dataset=dataset,
        data_version=data_version,
        ingestion_timestamp=ingestion_timestamp,
        checksum=checksum,
    )

    schema_with_meta = attach_metadata(dataset.schema, metadata)
    table = table.cast(schema_with_meta)

    root = dataset_version_root(base_path, dataset, data_version)

    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=list(dataset.partition_cols),
        existing_data_behavior="overwrite_or_ignore",
    )

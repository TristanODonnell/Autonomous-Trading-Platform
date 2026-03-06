from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from .datasets import ParquetDataset
from .metadata import attach_metadata, build_metadata
from .paths import dataset_root


def write_table(
    table: pa.Table,
    dataset: ParquetDataset,
    base_path: str | Path,
    data_version: str,
) -> None:
    """
    Write a PyArrow table to a Parquet dataset with partitioning and metadata.
    """

    # Build metadata
    metadata = build_metadata(dataset, data_version)

    # Attach metadata to schema
    schema_with_meta = attach_metadata(dataset.schema, metadata)

    # Enforce schema
    table = table.cast(schema_with_meta)

    root = dataset_root(base_path, dataset)

    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=list(dataset.partition_cols),
        existing_data_behavior="overwrite_or_ignore",
    )

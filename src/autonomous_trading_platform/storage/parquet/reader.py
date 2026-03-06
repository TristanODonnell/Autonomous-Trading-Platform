from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from .datasets import ParquetDataset
from .metadata import extract_metadata, validate_required_metadata
from .paths import dataset_root


def read_dataset(
    dataset: ParquetDataset,
    base_path: str | Path,
) -> pa.Table:
    """
    Read an entire Parquet dataset and validate metadata.
    """

    root = dataset_root(base_path, dataset)

    dataset_obj = ds.dataset(str(root), format="parquet")

    table = dataset_obj.to_table()

    metadata = extract_metadata(table.schema)

    validate_required_metadata(metadata)

    if metadata.get("schema_version") != dataset.schema_version:
        raise ValueError(
            f"Schema version mismatch: expected {dataset.schema_version}, "
            f"found {metadata.get('schema_version')}"
        )

    return table

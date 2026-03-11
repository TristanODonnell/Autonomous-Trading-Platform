from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from .datasets import ParquetDataset
from .helpers.compute_checksum import compute_table_checksum
from .metadata import extract_metadata, validate_required_metadata
from .paths import dataset_version_root


def read_dataset(
    dataset: ParquetDataset,
    base_path: str | Path,
    data_version: str,
) -> pa.Table:
    """
    Read an entire Parquet dataset and validate metadata.
    """

    root = dataset_version_root(base_path, dataset, data_version)

    dataset_obj = ds.dataset(str(root), format="parquet")

    table = dataset_obj.to_table()

    metadata = extract_metadata(table.schema)

    validate_required_metadata(metadata)

    stored_checksum = metadata.get("checksum")
    actual_checksum = compute_table_checksum(table)
    if stored_checksum is None:
        raise ValueError("Missing checksum in dataset metadata")

    if stored_checksum != actual_checksum:
        raise ValueError(f"Checksum mismatch: expected {stored_checksum}, found {actual_checksum}")

    if metadata.get("schema_version") != dataset.schema_version:
        raise ValueError(
            f"Schema version mismatch: expected {dataset.schema_version}, "
            f"found {metadata.get('schema_version')}"
        )
    if metadata.get("dataset_name") != dataset.name:
        raise ValueError(
            f"Dataset mismatch: expected {dataset.name}, found {metadata.get('dataset_name')}"
        )

    return table


def list_partition_files(
    dataset: ParquetDataset,
    base_path: str | Path,
    data_version: str,
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[Path]:
    """Return parquet files for a dataset version, symbol, and date range."""
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    root = dataset_version_root(base_path, dataset, data_version)

    files: list[Path] = []
    current = start_date

    while current <= end_date:
        partition_dir = root / f"symbol={symbol.upper()}" / f"date={current.isoformat()}"

        if partition_dir.exists():
            files.extend(sorted(partition_dir.glob("*.parquet")))

        current += timedelta(days=1)

    return files

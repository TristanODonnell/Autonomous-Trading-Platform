from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

from .datasets import ParquetDataset
from .metadata import extract_metadata, validate_required_metadata
from .paths import dataset_version_root


def read_dataset(
    dataset: ParquetDataset,
    base_path: str | Path,
    dataset_version: str,
) -> pa.Table:
    """
    Read an entire Parquet dataset and validate metadata.
    """

    root = dataset_version_root(base_path, dataset, dataset_version)

    dataset_obj = ds.dataset(str(root), format="parquet")
    table = dataset_obj.to_table()

    metadata = extract_metadata(table.schema)
    validate_required_metadata(metadata)

    if metadata.get("schema_version") != dataset.schema_version:
        raise ValueError(
            f"Schema version mismatch: expected {dataset.schema_version}, "
            f"found {metadata.get('schema_version')}"
        )

    if metadata.get("dataset_name") != dataset.dataset_key:
        raise ValueError(
            f"Dataset mismatch: expected {dataset.dataset_key}, "
            f"found {metadata.get('dataset_name')}"
        )

    return table


def _month_partitions_between(start_date: date, end_date: date) -> list[tuple[str, str]]:
    """
    Return unique (year, month) partition values between two dates, inclusive.
    Month is zero-padded, e.g. '02'.
    """
    if end_date < start_date:
        raise ValueError("end_date must be greater than or equal to start_date")

    partitions: list[tuple[str, str]] = []

    year = start_date.year
    month = start_date.month

    while (year, month) <= (end_date.year, end_date.month):
        partitions.append((f"{year:04d}", f"{month:02d}"))

        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return partitions


def list_partition_files(
    dataset: ParquetDataset,
    base_path: str | Path,
    dataset_version: str,
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[Path]:
    """
    Return parquet files for a dataset version, symbol, and date range.

    Note:
    This resolves month partitions only. Date filtering within a month should
    happen after reading the parquet data.
    """
    root = dataset_version_root(base_path, dataset, dataset_version)

    files: list[Path] = []
    month_partitions = _month_partitions_between(start_date, end_date)

    for year_value, month_value in month_partitions:
        partition_dir = (
            root / f"symbol={symbol.upper()}" / f"year={year_value}" / f"month={month_value}"
        )

        if partition_dir.exists():
            files.extend(sorted(partition_dir.glob("*.parquet")))

    return files

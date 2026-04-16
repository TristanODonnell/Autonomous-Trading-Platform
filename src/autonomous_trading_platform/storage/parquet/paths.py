from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .datasets import ParquetDataset


def format_partition_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, str):
        return value.upper() if value.isalpha() and len(value) <= 10 else value

    return str(value)


def dataset_version_root(
    base_path: str | Path,
    dataset: ParquetDataset,
    dataset_version: str,
) -> Path:
    root = Path(base_path)
    for part in dataset.root_parts:
        root = root / part
    return root / f"dataset_version={dataset_version}"


def partition_path(
    base_path: str | Path,
    dataset: ParquetDataset,
    dataset_version: str,
    partitions: dict[str, Any],
) -> Path:
    path = dataset_version_root(base_path, dataset, dataset_version)

    for col in dataset.partition_cols:
        if col not in partitions:
            raise ValueError(f"Missing partition value for column: {col}")
        path = path / f"{col}={format_partition_value(partitions[col])}"

    return path


def partition_file_path(
    base_path: str | Path,
    dataset: ParquetDataset,
    dataset_version: str,
    partitions: dict[str, Any],
    filename: str = "data.parquet",
) -> Path:
    return partition_path(base_path, dataset, dataset_version, partitions) / filename

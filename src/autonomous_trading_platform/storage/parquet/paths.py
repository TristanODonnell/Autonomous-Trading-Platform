from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from .datasets import ParquetDataset


def dataset_version_root(
    base_path: str | Path,
    dataset: ParquetDataset,
    data_version: str,
) -> Path:
    return Path(base_path) / dataset.name / data_version


def format_partition_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, str):
        return value.upper() if value.isalpha() and len(value) <= 10 else value

    return str(value)


def partition_path(
    base_path: str | Path,
    dataset: ParquetDataset,
    data_version: str,
    partitions: dict[str, Any],
) -> Path:
    root = dataset_version_root(base_path, dataset, data_version)

    path = root
    for col in dataset.partition_cols:
        if col not in partitions:
            raise ValueError(f"Missing partition value for column: {col}")
        formatted = format_partition_value(partitions[col])
        path = path / f"{col}={formatted}"

    return path

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from autonomous_trading_platform.storage.parquet.compute_checksum import compute_table_checksum

from .datasets import (
    ADJUSTED_BARS_DATASET,
    CORPORATE_ACTIONS_DATASET,
    RAW_BARS_DATASET,
    ParquetDataset,
)
from .metadata import (
    attach_metadata,
    build_dataset_artifact_metadata,
    build_metadata,
)
from .paths import dataset_version_root


def _has_column(table: pa.Table, column_name: str) -> bool:
    return column_name in table.schema.names


def add_bar_partition_columns(table: pa.Table) -> pa.Table:
    result = table

    date_values: list[date | None]

    if _has_column(result, "date"):
        date_values = result["date"].to_pylist()
    else:
        timestamp_values = result["timestamp"].to_pylist()
        date_values = []

        for value in timestamp_values:
            if value is None:
                date_values.append(None)
                continue

            if isinstance(value, datetime):
                if value.tzinfo is not None:
                    value = value.astimezone(UTC).replace(tzinfo=None)
                date_values.append(value.date())
                continue

            raise TypeError(f"Unsupported timestamp value type for partitioning: {type(value)!r}")

        result = result.append_column("date", pa.array(date_values, type=pa.date32()))

    if not _has_column(result, "year"):
        years = [str(value.year) if value is not None else None for value in date_values]
        result = result.append_column("year", pa.array(years, type=pa.string()))

    if not _has_column(result, "month"):
        months = [f"{value.month:02d}" if value is not None else None for value in date_values]
        result = result.append_column("month", pa.array(months, type=pa.string()))

    return result


def add_corporate_action_partition_columns(table: pa.Table) -> pa.Table:
    result = table
    effective_dates = result["effective_date"]

    if not _has_column(result, "date"):
        result = result.append_column("date", effective_dates)

    if not _has_column(result, "year"):
        years = pc.strftime(effective_dates, format="%Y")
        result = result.append_column("year", years)

    if not _has_column(result, "month"):
        months = pc.strftime(effective_dates, format="%m")
        result = result.append_column("month", months)

    return result


def prepare_partition_columns(
    table: pa.Table,
    dataset: ParquetDataset,
) -> pa.Table:
    if dataset in (RAW_BARS_DATASET, ADJUSTED_BARS_DATASET):
        return add_bar_partition_columns(table)

    if dataset == CORPORATE_ACTIONS_DATASET:
        return add_corporate_action_partition_columns(table)

    return table


def _list_parquet_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def _write_artifact_metadata_file(root: Path, metadata: dict) -> None:
    metadata_path = root / "_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _ensure_dataset_version_is_new(root: Path) -> None:
    if not root.exists():
        return

    existing_parquet_files = list(root.rglob("*.parquet"))
    metadata_file = root / "_metadata.json"

    if existing_parquet_files or metadata_file.exists():
        raise FileExistsError(f"Dataset version already exists and is immutable: {root}")


def write_table(
    table: pa.Table,
    dataset: ParquetDataset,
    base_path: str | Path,
    dataset_version: str,
    *,
    allow_existing: bool = False,
) -> None:
    ingestion_timestamp = datetime.now(UTC).isoformat()

    table = prepare_partition_columns(table, dataset)
    row_count = table.num_rows
    checksum = compute_table_checksum(table)

    metadata = build_metadata(
        dataset=dataset,
        dataset_version=dataset_version,
        ingestion_timestamp=ingestion_timestamp,
        checksum=checksum,
    )

    schema_with_meta = attach_metadata(dataset.schema, metadata)
    table = table.cast(schema_with_meta)

    root = dataset_version_root(base_path, dataset, dataset_version)

    if not allow_existing:
        _ensure_dataset_version_is_new(root)

    partition_fields = [dataset.schema.field(col) for col in dataset.partition_cols]
    hive_partitioning = ds.partitioning(pa.schema(partition_fields), flavor="hive")

    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=hive_partitioning,
        existing_data_behavior="overwrite_or_ignore" if allow_existing else "error",
        basename_template=f"part-{uuid.uuid4().hex}-{{i}}.parquet",
    )

    parquet_files = _list_parquet_files(root)
    file_count = len(parquet_files)
    total_file_size_bytes = sum(path.stat().st_size for path in parquet_files)

    artifact_metadata = build_dataset_artifact_metadata(
        dataset=dataset,
        dataset_version=dataset_version,
        ingestion_timestamp=ingestion_timestamp,
        checksum=checksum,
        row_count=row_count,
        file_count=file_count,
        total_file_size_bytes=total_file_size_bytes,
        partition_columns=dataset.partition_cols,
    )

    _write_artifact_metadata_file(root, artifact_metadata)

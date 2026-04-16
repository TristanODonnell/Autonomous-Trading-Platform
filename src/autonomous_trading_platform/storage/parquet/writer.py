from __future__ import annotations

from datetime import UTC, datetime
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
from .metadata import attach_metadata, build_metadata
from .paths import dataset_version_root


def _has_column(table: pa.Table, column_name: str) -> bool:
    return column_name in table.schema.names


def add_bar_partition_columns(table: pa.Table) -> pa.Table:
    """
    Add date/year/month partition columns for bar datasets using timestamp.
    """
    result = table

    timestamps = result["timestamp"]

    if not _has_column(result, "date"):
        date_strings = pc.strftime(timestamps, format="%Y-%m-%d")
        result = result.append_column(
            "date", pc.strptime(date_strings, format="%Y-%m-%d", unit="s").cast(pa.date32())
        )

    if not _has_column(result, "year"):
        years = pc.strftime(timestamps, format="%Y")
        result = result.append_column("year", years)

    if not _has_column(result, "month"):
        months = pc.strftime(timestamps, format="%m")
        result = result.append_column("month", months)

    return result


def add_corporate_action_partition_columns(table: pa.Table) -> pa.Table:
    """
    Add date/year/month partition columns for corporate action datasets using effective_date.
    """
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


def write_table(
    table: pa.Table,
    dataset: ParquetDataset,
    base_path: str | Path,
    dataset_version: str,
) -> None:
    """
    Write a PyArrow table to a Parquet dataset with partitioning and metadata.
    """
    ingestion_timestamp = datetime.now(UTC).isoformat()

    table = prepare_partition_columns(table, dataset)
    table = table.cast(dataset.schema)

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

    ds.write_dataset(
        table,
        base_dir=str(root),
        format="parquet",
        partitioning=list(dataset.partition_cols),
        existing_data_behavior="overwrite_or_ignore",
    )

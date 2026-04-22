from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.dataset as ds
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.errors.errors import DatasetCorruptionError
from autonomous_trading_platform.storage.parquet.compute_checksum import compute_file_checksum
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork

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


class HistoricalBarDatasetReader:
    def __init__(self, session: Session, *, base_path: str | Path = "data") -> None:
        self.session = session
        self.base_path = Path(base_path)

    def read_with_pyarrow(
        self,
        *,
        dataset: ParquetDataset,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pa.Table:
        symbol = symbol.upper()
        files = list_partition_files(
            dataset=dataset,
            base_path=self.base_path,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if not files:
            return pa.table([], schema=dataset.schema)

        start_ts, end_ts = self._date_range_to_timestamps(start_date, end_date)

        dataset_obj = ds.dataset([str(f) for f in files], format="parquet")

        filter_expr = (
            (ds.field("symbol") == symbol)
            & (ds.field("timestamp") >= pa.scalar(start_ts))
            & (ds.field("timestamp") < pa.scalar(end_ts))
        )

        table = dataset_obj.to_table(filter=filter_expr)

        return table.sort_by([("timestamp", "ascending")]) if table.num_rows > 0 else table

    def read_with_duckdb(
        self,
        *,
        dataset: ParquetDataset,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pa.Table:
        symbol = symbol.upper()
        files = list_partition_files(
            dataset=dataset,
            base_path=self.base_path,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if not files:
            return pa.table([], schema=dataset.schema)

        start_ts, end_ts = self._date_range_to_timestamps(start_date, end_date)

        con = duckdb.connect(database=":memory:")

        try:
            file_list = [str(f) for f in files]

            result = con.execute(
                f"""
                SELECT *
                FROM parquet_scan({file_list})
                WHERE symbol = ?
                  AND timestamp >= ?
                  AND timestamp < ?
                ORDER BY timestamp ASC
                """,
                [symbol, start_ts, end_ts],
            )

            return result.arrow()
        finally:
            con.close()

    def read(
        self,
        *,
        dataset: ParquetDataset,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
        engine: str = "duckdb",
    ) -> pa.Table:
        symbol = symbol.upper()
        if engine == "duckdb":
            return self.read_with_duckdb(
                dataset=dataset,
                dataset_version=dataset_version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

        if engine == "pyarrow":
            return self.read_with_pyarrow(
                dataset=dataset,
                dataset_version=dataset_version,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

        raise ValueError(f"Unsupported engine: {engine}")

    @staticmethod
    def _date_range_to_timestamps(start_date: date, end_date: date) -> tuple[datetime, datetime]:
        start_ts = datetime.combine(start_date, time.min, tzinfo=UTC)
        end_ts = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
        return start_ts, end_ts

    def _verify_file_checksums(self, *, dataset_version: str, files: list[Path]) -> None:
        with SorUnitOfWork(self.session) as uow:
            for path in files:
                checksum_row = uow.checksums.get_by_dataset_version_and_object_path(
                    dataset_version=dataset_version,
                    object_path=str(path),
                )
                if checksum_row is None:
                    raise DatasetCorruptionError(
                        f"Missing checksum record for dataset_version={dataset_version}, file={path}"
                    )

                actual = compute_file_checksum(path)
                if actual != checksum_row.checksum_value:
                    raise DatasetCorruptionError(
                        f"Checksum mismatch for {path}: expected {checksum_row.checksum_value}, got {actual}"
                    )

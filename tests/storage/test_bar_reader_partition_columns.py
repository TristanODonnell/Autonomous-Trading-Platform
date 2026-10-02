"""Bar reads return the partition columns, whatever files back them.

``part-*.parquet`` fragments store symbol / year / month only in their path. The
pyarrow reader (the default engine since DuckDB reads leaked ~150 MB of native memory
per call inside the test process) must add them, as DuckDB's parquet_scan does —
the returns feature job failed on a null ``symbol`` without them.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader
from tests.utilities.parity_harness import (
    PARITY_DATASET_VERSION,
    PARITY_DAYS,
    write_parity_bars,
)


def test_default_read_returns_symbol_year_and_month(db_session, tmp_path: Path) -> None:
    root = tmp_path / "data"
    write_parity_bars(root)
    fragments = list(root.glob("**/symbol=BBB/**/part-*.parquet"))
    assert fragments, "fixture should write fragment files (no compacted data.parquet)"

    table = HistoricalBarDatasetReader(db_session, base_path=root).read(
        dataset=RAW_BARS_DATASET,
        dataset_version=PARITY_DATASET_VERSION,
        symbol="bbb",
        start_date=PARITY_DAYS[0],
        end_date=PARITY_DAYS[-1],
    )

    assert table.num_rows == 78 * len(PARITY_DAYS)
    assert set(table.column("symbol").to_pylist()) == {"BBB"}
    assert set(table.column("year").to_pylist()) == {"2024"}
    assert set(table.column("month").to_pylist()) == {"01"}
    timestamps = table.column("timestamp").to_pylist()
    assert timestamps == sorted(timestamps)
    assert timestamps[0].date() == PARITY_DAYS[0]


def test_empty_range_returns_an_empty_table(db_session, tmp_path: Path) -> None:
    root = tmp_path / "data"
    write_parity_bars(root)
    table = HistoricalBarDatasetReader(db_session, base_path=root).read(
        dataset=RAW_BARS_DATASET,
        dataset_version=PARITY_DATASET_VERSION,
        symbol="BBB",
        start_date=date(2023, 6, 1),
        end_date=date(2023, 6, 2),
    )
    assert table.num_rows == 0

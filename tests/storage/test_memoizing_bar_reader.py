"""One trading cycle reads each (dataset, version, symbol, window) from Parquet once;
every strategy's context build and the volatility scalar then get the same table."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pyarrow as pa

from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import (
    HistoricalBarDatasetReader,
    MemoizingBarDatasetReader,
)


def _read(
    reader: MemoizingBarDatasetReader, symbol: str, end_date: date = date(2024, 6, 10)
) -> pa.Table:
    return reader.read(
        dataset=RAW_BARS_DATASET,
        dataset_version="v",
        symbol=symbol,
        start_date=date(2024, 6, 3),
        end_date=end_date,
    )


def test_repeated_reads_of_one_window_hit_parquet_once() -> None:
    reader = MemoizingBarDatasetReader(MagicMock())
    table = pa.table({"close": [1.0, 2.0]})
    with patch.object(HistoricalBarDatasetReader, "read", return_value=table) as base_read:
        first = _read(reader, "nvda")
        second = _read(reader, "NVDA")
        third = _read(reader, "NVDA")

    assert first is table and second is table and third is table
    assert base_read.call_count == 1
    assert (reader.misses, reader.hits) == (1, 2)


def test_a_different_symbol_or_window_is_its_own_read() -> None:
    reader = MemoizingBarDatasetReader(MagicMock())
    with patch.object(
        HistoricalBarDatasetReader, "read", side_effect=lambda **kw: pa.table({"s": [kw["symbol"]]})
    ) as base_read:
        _read(reader, "NVDA")
        _read(reader, "AAPL")
        _read(reader, "NVDA", end_date=date(2024, 6, 11))
        _read(reader, "NVDA")

    assert base_read.call_count == 3
    assert (reader.misses, reader.hits) == (3, 1)

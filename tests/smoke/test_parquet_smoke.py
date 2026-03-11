from datetime import date, datetime

import pyarrow as pa

from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import read_dataset
from autonomous_trading_platform.storage.parquet.writer import write_table


def test_parquet_write_read(tmp_path):

    data = {
        "bar_id": ["bar1"],
        "timestamp": [datetime.utcnow()],
        "end_timestamp": [datetime.utcnow()],
        "interval": ["1m"],
        "symbol": ["AAPL"],
        "open": [190.0],
        "high": [191.0],
        "low": [189.5],
        "close": [190.5],
        "volume": [1000],
        "vwap": [190.3],
        "trade_count": [50],
        "price_basis": ["raw"],
        "adjustment_factor": [1.0],
        "source": ["alpaca"],
        "ingested_at": [datetime.utcnow()],
        "quality_flags": [["ok"]],
        "date": [date.today()],
    }

    table = pa.table(data)
    data_version = "test-run"
    write_table(
        table=table,
        dataset=RAW_BARS_DATASET,
        base_path=tmp_path,
        data_version=data_version,
    )

    result = read_dataset(RAW_BARS_DATASET, tmp_path, data_version)

    assert result.num_rows == 1

from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.paths import partition_file_path


class BarChunkWriterService:
    def __init__(self, base_path: str = "data") -> None:
        self.base_path = Path(base_path)

    def write_backfill_chunk(
        self,
        *,
        bars: list[MarketBar],
        dataset_version: str,
        symbol: str,
        bar_date: date,
    ) -> None:
        if not bars:
            return

        table = bars_to_arrow(bars)

        output_path = partition_file_path(
            base_path=self.base_path,
            dataset=RAW_BARS_DATASET,
            dataset_version=dataset_version,
            partitions={
                "symbol": symbol,
                "year": f"{bar_date.year:04d}",
                "month": f"{bar_date.month:02d}",
            },
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            output_path.unlink()

        pq.write_table(
            table,
            output_path,
        )

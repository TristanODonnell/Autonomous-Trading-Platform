from __future__ import annotations

from datetime import date
from pathlib import Path

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.writer import write_table


class BarChunkWriterService:
    def __init__(self, base_path: str = "data"):
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

        # determine partition path
        year = bar_date.year
        month = bar_date.month

        partition_path = (
            self.base_path
            / "bars"
            / "raw"
            / f"dataset_version={dataset_version}"
            / f"symbol={symbol}"
            / f"year={year}"
            / f"month={month}"
        )

        self._replace_partition(partition_path)

        write_table(
            table=table,
            dataset=RAW_BARS_DATASET,
            base_path=str(self.base_path),
            dataset_version=dataset_version,
        )

    def _replace_partition(self, partition_path: Path) -> None:
        if not partition_path.exists():
            return

        for file in partition_path.glob("*.parquet"):
            file.unlink()

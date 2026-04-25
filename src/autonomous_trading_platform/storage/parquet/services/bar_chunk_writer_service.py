from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.mappers import bars_to_arrow
from autonomous_trading_platform.storage.parquet.paths import (
    dataset_version_root,
    partition_file_path,
)


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
        root = dataset_version_root(
            self.base_path,
            RAW_BARS_DATASET,
            dataset_version,
        )

        root.mkdir(parents=True, exist_ok=True)

        metadata_path = root / "_metadata.json"

        metadata = {
            "dataset_version": dataset_version,
            "dataset": "raw_bars",
            "last_updated_at": datetime.now(UTC).isoformat(),
        }

        metadata_path.write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )

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

        pq.write_table(table, output_path)

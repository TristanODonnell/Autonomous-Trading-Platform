from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import (
    BarInterval,
    BarQualityFlag,
    MarketSession,
    PriceBasis,
)
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.parquet import paths as _parquet_paths
from autonomous_trading_platform.storage.parquet.datasets import RAW_BARS_DATASET
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader


class ParquetBarRepository:
    def __init__(self, *, base_path: Path | str | None = None) -> None:
        session: Session = get_session()
        self.base_path = (
            Path(base_path) if base_path is not None else _parquet_paths.get_data_root()
        )
        self.reader = HistoricalBarDatasetReader(base_path=self.base_path, session=session)

    def get_raw_bars_before_date(
        self,
        *,
        symbol: str,
        dataset_version: str,
        effective_date: date | datetime,
    ) -> list[MarketBar]:
        cutoff = self._normalize_effective_date(effective_date)
        symbol = symbol.upper()

        dataset_root = self.base_path / "bars" / "raw" / f"dataset_version={dataset_version}"
        if not dataset_root.exists():
            return []

        table = self.reader.read(
            dataset=RAW_BARS_DATASET,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=date(1970, 1, 1),
            end_date=cutoff.date(),
            engine="duckdb",
        )

        if table.num_rows == 0:
            return []

        rows = [
            row for row in table.to_pylist() if self._as_utc_datetime(row["timestamp"]) < cutoff
        ]

        if not rows:
            return []

        rows.sort(key=lambda row: row["timestamp"])
        return [self._row_to_market_bar(row) for row in rows]

    @staticmethod
    def _normalize_effective_date(value: date | datetime) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        return datetime(value.year, value.month, value.day, tzinfo=UTC)

    @staticmethod
    def _row_to_market_bar(row: dict) -> MarketBar:
        timestamp = ParquetBarRepository._as_utc_datetime(row["timestamp"])
        end_timestamp = ParquetBarRepository._as_utc_datetime(row["end_timestamp"])
        ingested_at = ParquetBarRepository._as_utc_datetime(row["ingested_at"])

        quality_flags_raw = row.get("quality_flags") or []
        quality_flags = [
            flag if isinstance(flag, BarQualityFlag) else BarQualityFlag(flag)
            for flag in quality_flags_raw
        ]

        session_raw = row.get("session", MarketSession.REGULAR.value)

        return MarketBar(
            bar_id=str(row["bar_id"]),
            timestamp=timestamp,
            end_timestamp=end_timestamp,
            interval=row["interval"]
            if isinstance(row["interval"], BarInterval)
            else BarInterval(row["interval"]),
            symbol=str(row["symbol"]),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=int(row["volume"]),
            vwap=Decimal(str(row["vwap"])) if row.get("vwap") is not None else None,
            trade_count=int(row["trade_count"]) if row.get("trade_count") is not None else None,
            price_basis=row["price_basis"]
            if isinstance(row["price_basis"], PriceBasis)
            else PriceBasis(row["price_basis"]),
            adjustment_factor=Decimal(str(row["adjustment_factor"])),
            source=str(row["source"]),
            ingested_at=ingested_at,
            quality_flags=quality_flags,
            session=(
                session_raw
                if isinstance(session_raw, MarketSession)
                else MarketSession(session_raw)
            ),
        )

    @staticmethod
    def _as_utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def read(
        self,
        *,
        dataset,
        dataset_version: str,
        symbol: str,
        start_date: date,
        end_date: date,
        engine: str = "duckdb",
    ):
        return self.reader.read(
            dataset=dataset,
            dataset_version=dataset_version,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            engine=engine,
        )

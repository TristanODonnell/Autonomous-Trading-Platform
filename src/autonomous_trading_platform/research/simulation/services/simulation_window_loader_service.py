from __future__ import annotations

import copy
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.storage.parquet.datasets import (
    ParquetDataset,
)
from autonomous_trading_platform.storage.parquet.reader import HistoricalBarDatasetReader


def shuffle_window_bar_timestamps(
    window: SimulationWindowData,
    *,
    random_seed: int,
) -> SimulationWindowData:
    rng = random.Random(random_seed)

    shuffled_bars_by_symbol = {}

    for symbol, bars in window.bars_by_symbol.items():
        timestamps = [bar.timestamp for bar in bars]
        rng.shuffle(timestamps)

        shuffled_bars = []

        for bar, shuffled_timestamp in zip(bars, timestamps, strict=True):
            copied_bar = copy.copy(bar)
            copied_bar.timestamp = shuffled_timestamp
            shuffled_bars.append(copied_bar)

        shuffled_bars_by_symbol[symbol] = sorted(
            shuffled_bars,
            key=lambda bar: bar.timestamp,
        )

    shuffled_bars_by_timestamp: dict[datetime, dict[str, MarketBar]] = {}

    for symbol, bars in shuffled_bars_by_symbol.items():
        for bar in bars:
            shuffled_bars_by_timestamp.setdefault(bar.timestamp, {})[symbol] = bar

    shuffled_timeline = sorted(shuffled_bars_by_timestamp.keys())

    shuffled_timestamps_by_symbol = {
        symbol: [b.timestamp for b in bars] for symbol, bars in shuffled_bars_by_symbol.items()
    }

    return SimulationWindowData(
        start_date=window.start_date,
        end_date=window.end_date,
        dataset_version=window.dataset_version,
        symbols=window.symbols,
        timeline=shuffled_timeline,
        bars_by_symbol=shuffled_bars_by_symbol,
        bars_by_timestamp=shuffled_bars_by_timestamp,
        feature_tables_by_symbol=window.feature_tables_by_symbol,
        warmup_bars_by_symbol=window.warmup_bars_by_symbol,
        warmup_timestamps=window.warmup_timestamps,
        sorted_timestamps_by_symbol=shuffled_timestamps_by_symbol,
    )


@dataclass(slots=True)
class SimulationFeatureDatasetRequest:
    feature_name: str
    dataset: ParquetDataset
    dataset_version: str


@dataclass(slots=True)
class SimulationWindowData:
    start_date: date
    end_date: date
    dataset_version: str
    symbols: list[str]
    timeline: list[datetime]
    bars_by_symbol: dict[str, list[MarketBar]]
    bars_by_timestamp: dict[datetime, dict[str, MarketBar]]

    # Always present. Empty dict means no features loaded.
    feature_tables_by_symbol: dict[str, dict[str, pa.Table]]

    # Warmup bars come before start_date and are used to seed indicator state
    # only — the simulation engine must not generate trades on these timestamps.
    # Empty when warmup_bars=0 (the default).
    warmup_bars_by_symbol: dict[str, list[MarketBar]] = None  # type: ignore[assignment]
    warmup_timestamps: set[datetime] = None  # type: ignore[assignment]

    # Precomputed sorted timestamp lists per symbol for O(log N) bisect lookups
    # in build_from_window. Populated by SimulationWindowLoader.load_window().
    sorted_timestamps_by_symbol: dict[str, list[datetime]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warmup_bars_by_symbol is None:
            self.warmup_bars_by_symbol = {}
        if self.warmup_timestamps is None:
            self.warmup_timestamps = set()
        if self.sorted_timestamps_by_symbol is None:
            self.sorted_timestamps_by_symbol = {
                symbol: [b.timestamp for b in bars] for symbol, bars in self.bars_by_symbol.items()
            }

    def is_warmup(self, timestamp: datetime) -> bool:
        """Return True if this timestamp belongs to the warmup period.
        The simulation engine should skip order generation for warmup bars."""
        return timestamp in self.warmup_timestamps


class SimulationWindowLoader:
    """
    Loads simulation data using bounded partition reads only.
    This loader must not read entire dataset versions for windowed simulation access.
    """

    def __init__(
        self,
        *,
        bar_reader: HistoricalBarDatasetReader,
        feature_reader: HistoricalBarDatasetReader | None = None,
    ) -> None:
        self.bar_reader = bar_reader
        self.feature_reader = feature_reader

    # 5-min bars per trading day (9:30→16:00 = 6.5h = 78 bars)
    _BARS_PER_TRADING_DAY: int = 78
    # Conservative calendar-day multiplier to guarantee enough trading days
    # are fetched even across weekends/holidays (5 trading days per ~7 cal days)
    _CALENDAR_DAYS_PER_TRADING_DAY: float = 7 / 5

    def load_window(
        self,
        *,
        dataset_version: str,
        bars_dataset: ParquetDataset,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        feature_datasets: Iterable[SimulationFeatureDatasetRequest] | None = None,
        engine: str = "duckdb",
        strict: bool = False,
        warmup_bars: int = 0,
        resample_to_daily: bool = False,
    ) -> SimulationWindowData:
        """Load bar and feature data for a simulation window.

        Parameters
        ----------
        warmup_bars:
            Number of bars to load *before* start_date to seed indicator state.
            These bars are included in ``bars_by_symbol`` and ``timeline`` so the
            strategy context builder can warm up MAs/RSI/etc., but they are
            flagged in ``warmup_timestamps`` so the execution engine skips order
            generation on them.  Pass ``strategy.long_window * bars_per_day``
            (e.g. 200 * 78 = 15_600) to fully warm up a 200-bar MA.
            Defaults to 0 (no warmup, existing behaviour preserved).
        """
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")

        if not dataset_version.strip():
            raise ValueError("dataset_version must not be empty")

        if warmup_bars < 0:
            raise ValueError("warmup_bars must be >= 0")

        normalized_symbols = sorted(
            {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        )
        if not normalized_symbols:
            raise ValueError("At least one symbol is required")

        # Compute the extended read start to cover warmup bars.
        # We over-fetch slightly (ceiling) to account for weekends/holidays,
        # then trim to the exact bar count after reading.
        from datetime import timedelta
        from math import ceil

        if warmup_bars > 0:
            warmup_trading_days = ceil(warmup_bars / self._BARS_PER_TRADING_DAY)
            warmup_calendar_days = ceil(warmup_trading_days * self._CALENDAR_DAYS_PER_TRADING_DAY)
            read_start = start_date - timedelta(days=warmup_calendar_days)
        else:
            read_start = start_date

        bars_by_symbol: dict[str, list[MarketBar]] = {}
        feature_requests = list(feature_datasets or [])
        feature_tables_by_symbol: dict[str, dict[str, pa.Table]] = {}

        for symbol in normalized_symbols:
            try:
                bar_table = self.bar_reader.read(
                    dataset=bars_dataset,
                    dataset_version=dataset_version,
                    symbol=symbol,
                    start_date=read_start,
                    end_date=end_date,
                    engine=engine,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load bar data for symbol={symbol}, "
                    f"dataset_version={dataset_version}, "
                    f"window={start_date}..{end_date}"
                ) from exc

            if strict and bar_table.num_rows == 0:
                raise ValueError(
                    f"No bar data found for symbol={symbol}, "
                    f"dataset_version={dataset_version}, "
                    f"window={start_date}..{end_date}"
                )

            all_bars = [self._row_to_market_bar(row) for row in bar_table.to_pylist()]

            # Ensure bars are always sorted by timestamp so bisect lookups in
            # build_from_window are correct. Sort once here rather than O(N) scan
            # per timestamp in the simulation loop.
            all_bars.sort(key=lambda b: b.timestamp)

            if warmup_bars > 0:
                pre_bars = [b for b in all_bars if b.timestamp.date() < start_date]
                live_bars = [b for b in all_bars if b.timestamp.date() >= start_date]
                # Trim pre_bars to exactly warmup_bars (take the most recent ones)
                warmup_slice = pre_bars[-warmup_bars:] if len(pre_bars) > warmup_bars else pre_bars
                bars_by_symbol[symbol] = warmup_slice + live_bars
            else:
                bars_by_symbol[symbol] = all_bars

            if feature_requests:
                if self.feature_reader is None:
                    raise ValueError(
                        "feature_reader is required when feature_datasets are provided"
                    )

                feature_tables_by_symbol[symbol] = {}

                for feature_request in feature_requests:
                    try:
                        feature_table = self.feature_reader.read(
                            dataset=feature_request.dataset,
                            dataset_version=feature_request.dataset_version,
                            symbol=symbol,
                            start_date=read_start,
                            end_date=end_date,
                            engine=engine,
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"Failed to load feature data for symbol={symbol}, "
                            f"feature_name={feature_request.feature_name}, "
                            f"dataset_version={feature_request.dataset_version}, "
                            f"window={start_date}..{end_date}, "
                            f"feature_dataset={feature_request.dataset.dataset_key}"
                        ) from exc

                    if strict and feature_table.num_rows == 0:
                        raise ValueError(
                            f"No feature data found for symbol={symbol}, "
                            f"feature_name={feature_request.feature_name}, "
                            f"dataset_version={feature_request.dataset_version}, "
                            f"window={start_date}..{end_date}"
                        )

                    feature_tables_by_symbol[symbol][feature_request.feature_name] = feature_table

        if resample_to_daily:
            bars_by_symbol = self._resample_bars_to_daily(bars_by_symbol)

        bars_by_timestamp: dict[datetime, dict[str, MarketBar]] = {}

        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                bars_by_timestamp.setdefault(bar.timestamp, {})[symbol] = bar

        timeline = sorted(bars_by_timestamp.keys())

        # Build warmup tracking structures
        warmup_bars_by_symbol: dict[str, list[MarketBar]] = {}
        warmup_timestamps: set[datetime] = set()

        if warmup_bars > 0:
            for symbol, bars in bars_by_symbol.items():
                w_bars = [b for b in bars if b.timestamp.date() < start_date]
                if w_bars:
                    warmup_bars_by_symbol[symbol] = w_bars
                    warmup_timestamps.update(b.timestamp for b in w_bars)

        return SimulationWindowData(
            start_date=start_date,
            end_date=end_date,
            dataset_version=dataset_version,
            symbols=normalized_symbols,
            timeline=timeline,
            bars_by_symbol=bars_by_symbol,
            bars_by_timestamp=bars_by_timestamp,
            feature_tables_by_symbol=feature_tables_by_symbol,
            warmup_bars_by_symbol=warmup_bars_by_symbol,
            warmup_timestamps=warmup_timestamps,
        )

    @staticmethod
    def _resample_bars_to_daily(
        bars_by_symbol: dict[str, list[MarketBar]],
    ) -> dict[str, list[MarketBar]]:
        """Aggregate intraday bars to one OHLCV bar per symbol per trading day.

        Uses the last intraday bar's timestamp as the daily bar's timestamp so
        date-based warmup filtering (b.timestamp.date() < start_date) still works.
        """
        from collections import defaultdict

        from autonomous_trading_platform.contracts.common.enums import BarInterval, MarketSession

        daily: dict[str, list[MarketBar]] = {}
        for symbol, bars in bars_by_symbol.items():
            sorted_bars = sorted(bars, key=lambda b: b.timestamp)
            by_date: dict = defaultdict(list)
            for bar in sorted_bars:
                by_date[bar.timestamp.date()].append(bar)

            day_bars: list[MarketBar] = []
            for day in sorted(by_date.keys()):
                day_list: list[MarketBar] = by_date[day]
                first, last = day_list[0], day_list[-1]
                trade_count_sum = sum(b.trade_count for b in day_list if b.trade_count is not None)
                day_bars.append(
                    MarketBar(
                        bar_id=f"{symbol}_daily_{day.isoformat()}",
                        timestamp=last.timestamp,
                        end_timestamp=last.end_timestamp,
                        interval=BarInterval.ONE_DAY,
                        symbol=symbol,
                        open=first.open,
                        high=max(b.high for b in day_list),
                        low=min(b.low for b in day_list),
                        close=last.close,
                        volume=sum(b.volume for b in day_list),
                        vwap=None,
                        trade_count=trade_count_sum if trade_count_sum else None,
                        price_basis=first.price_basis,
                        adjustment_factor=first.adjustment_factor,
                        source=first.source,
                        ingested_at=first.ingested_at,
                        quality_flags=[],
                        session=MarketSession.REGULAR,
                    )
                )
            daily[symbol] = day_bars
        return daily

    @staticmethod
    def _row_to_market_bar(row: dict[str, Any]) -> MarketBar:
        from autonomous_trading_platform.storage.parquet.repositories.parquet_bar_repository import (
            ParquetBarRepository,
        )

        return ParquetBarRepository._row_to_market_bar(row)

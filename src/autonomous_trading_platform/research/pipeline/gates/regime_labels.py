"""
On-the-fly regime labelling for research windows.

Why not the persisted regime_classification dataset?
---------------------------------------------------
The platform replay feature hook computes features one day at a time, so no
persisted regime dataset ever covers a multi-week research window, and the
default 50/200-bar trend MAs never warm up on a single day of bars. RegimeStage
therefore classifies the window itself, using the same deterministic
RegimeClassificationService as the feature pipeline (TASK-2.2).

Method
------
1. Resample each symbol's bars to one bar per trading day (last close, summed
   volume) — regimes are a daily-horizon concept; 5-min labels would flip
   constantly and turn per-regime metrics into noise.
2. Run RegimeClassificationService on the daily frame with short windows that
   warm up inside a ~3-month research window.
3. Portfolio label per date = modal label across symbols (ties broken
   alphabetically, so the result is deterministic). Matches the modal rule in
   RegimeJoinService.join_equity_curve.
4. Attach each equity-curve bar to its date's label.

Labels are used for *attribution* ("how did the strategy do on bear days?"),
never as a trading signal, so labelling a day with its own end-of-day
classification does not leak information into the strategy's returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.feature_engineering.regimes.regime_classification_service import (
    RegimeClassificationService,
)
from autonomous_trading_platform.research.analysis.regimes.regime_bucket import REGIME_COLUMN_MAP

REGIME_COLUMNS: tuple[str, ...] = tuple(REGIME_COLUMN_MAP.values())


@dataclass(frozen=True)
class RegimeClassifierWindows:
    """Daily-bar window lengths for on-the-fly classification.

    Defaults are deliberately shorter than the feature pipeline's (50/200) so
    labels become available within a ~60-trading-day research window.
    """

    trend_short_window: int = 10
    trend_long_window: int = 20
    vol_window: int = 10
    liquidity_avg_window: int = 10
    zscore_window: int = 10
    high_percentile: float = 80.0
    low_percentile: float = 20.0

    def __post_init__(self) -> None:
        if self.trend_short_window <= 0 or self.trend_long_window <= 0:
            raise ValueError("trend windows must be positive")
        if self.trend_short_window >= self.trend_long_window:
            raise ValueError("trend_short_window must be < trend_long_window")
        if min(self.vol_window, self.liquidity_avg_window, self.zscore_window) <= 1:
            raise ValueError("vol/liquidity/zscore windows must be > 1")
        if not (0.0 < self.low_percentile < self.high_percentile < 100.0):
            raise ValueError("percentiles must satisfy 0 < low < high < 100")


def _modal(values: pd.Series) -> Any:
    non_null = values.dropna()
    if non_null.empty:
        return None
    return non_null.mode().iloc[0]


def resample_bars_to_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """Collapse intraday bars to one row per (symbol, date).

    Input needs columns: symbol, timestamp, close, volume.
    Output columns: symbol, timestamp (last bar of the day), date, close, volume.
    """
    required = {"symbol", "timestamp", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars frame missing columns: {sorted(missing)}")
    if bars.empty:
        return pd.DataFrame(columns=["symbol", "timestamp", "date", "close", "volume"])

    frame = bars[["symbol", "timestamp", "close", "volume"]].copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["close"] = frame["close"].astype("float64")
    frame["volume"] = frame["volume"].astype("float64")
    frame["date"] = frame["timestamp"].dt.date
    frame = frame.sort_values(["symbol", "timestamp"])

    daily = (
        frame.groupby(["symbol", "date"], sort=True)
        .agg(timestamp=("timestamp", "last"), close=("close", "last"), volume=("volume", "sum"))
        .reset_index()
    )
    return daily[["symbol", "timestamp", "date", "close", "volume"]]


def classify_daily_regimes(
    bars: pd.DataFrame,
    *,
    windows: RegimeClassifierWindows | None = None,
    classification_service: RegimeClassificationService | None = None,
) -> pd.DataFrame:
    """Return portfolio-level regime labels, one row per date.

    Columns: date + every regime_* column. Labels are None while a
    classifier is still warming up.
    """
    windows = windows or RegimeClassifierWindows()
    service = classification_service or RegimeClassificationService()

    daily = resample_bars_to_daily(bars)
    if daily.empty:
        return pd.DataFrame(columns=["date", *REGIME_COLUMNS])

    labelled = service.compute(
        daily[["symbol", "timestamp", "close", "volume"]],
        trend_short_window=windows.trend_short_window,
        trend_long_window=windows.trend_long_window,
        vol_window=windows.vol_window,
        liquidity_avg_window=windows.liquidity_avg_window,
        zscore_window=windows.zscore_window,
        high_percentile=windows.high_percentile,
        low_percentile=windows.low_percentile,
    )
    labelled["date"] = pd.to_datetime(labelled["timestamp"], utc=True).dt.date

    present = [c for c in REGIME_COLUMNS if c in labelled.columns]
    portfolio = labelled.groupby("date", sort=True)[present].agg(_modal).reset_index()
    for col in REGIME_COLUMNS:
        if col not in portfolio.columns:
            portfolio[col] = None
    return portfolio[["date", *REGIME_COLUMNS]]


def attach_daily_regimes(
    equity_curve: pd.DataFrame,
    daily_regimes: pd.DataFrame,
) -> pd.DataFrame:
    """Build a regime_data frame keyed on the equity curve's own timestamps.

    The result plugs straight into RegimeAnalysisService, whose join is an
    exact timestamp match. Bars on dates without a label get None.
    """
    if equity_curve.empty or "timestamp" not in equity_curve.columns:
        return pd.DataFrame(columns=["timestamp", *REGIME_COLUMNS])

    frame = equity_curve[["timestamp"]].copy()
    frame["date"] = pd.to_datetime(frame["timestamp"], utc=True).dt.date
    if daily_regimes.empty:
        for col in REGIME_COLUMNS:
            frame[col] = None
    else:
        frame = frame.merge(daily_regimes, on="date", how="left")
    return frame[["timestamp", *REGIME_COLUMNS]]


class RegimeLabelProvider(Protocol):
    def load_daily_regimes(
        self,
        *,
        dataset_version: str,
        price_basis: PriceBasis,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Return date + regime_* columns covering [start_date, end_date]."""
        ...


class OnTheFlyRegimeLabelProvider:
    """Reads the research bars for a window and classifies them in memory.

    warmup_calendar_days extends the read backwards so classifiers can warm up
    before the window starts. When the dataset starts later (e.g. a replay
    whose bars begin at the replay start), the first days of the window are
    simply unlabelled and excluded from per-regime metrics.
    """

    def __init__(
        self,
        *,
        bar_reader: Any,
        dataset_resolver: Any,
        windows: RegimeClassifierWindows | None = None,
        warmup_calendar_days: int = 45,
    ) -> None:
        if warmup_calendar_days < 0:
            raise ValueError("warmup_calendar_days must be >= 0")
        self._bar_reader = bar_reader
        self._dataset_resolver = dataset_resolver
        self._windows = windows or RegimeClassifierWindows()
        self._warmup_calendar_days = warmup_calendar_days

    @classmethod
    def from_simulation_runner(
        cls,
        simulation_runner: Any,
        *,
        windows: RegimeClassifierWindows | None = None,
        warmup_calendar_days: int = 45,
    ) -> OnTheFlyRegimeLabelProvider | None:
        """Reuse the runner's own reader + resolver so labels see the same bars.

        Returns None when the runner does not expose them (e.g. test doubles).
        """
        window_loader = getattr(simulation_runner, "window_loader", None)
        bar_reader = getattr(window_loader, "bar_reader", None)
        dataset_resolver = getattr(simulation_runner, "dataset_resolver", None)
        if bar_reader is None or dataset_resolver is None:
            return None
        return cls(
            bar_reader=bar_reader,
            dataset_resolver=dataset_resolver,
            windows=windows,
            warmup_calendar_days=warmup_calendar_days,
        )

    def load_daily_regimes(
        self,
        *,
        dataset_version: str,
        price_basis: PriceBasis,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        resolved = self._dataset_resolver.resolve_bars_dataset(
            dataset_version=dataset_version,
            price_basis=price_basis,
        )
        read_start = start_date - timedelta(days=self._warmup_calendar_days)

        frames: list[pd.DataFrame] = []
        for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
            table = self._bar_reader.read(
                dataset=resolved.dataset,
                dataset_version=dataset_version,
                symbol=symbol,
                start_date=read_start,
                end_date=end_date,
            )
            if table.num_rows == 0:
                continue
            frame = table.to_pandas()
            if "symbol" not in frame.columns:
                frame["symbol"] = symbol
            frames.append(frame[["symbol", "timestamp", "close", "volume"]])

        if not frames:
            return pd.DataFrame(columns=["date", *REGIME_COLUMNS])

        daily = classify_daily_regimes(pd.concat(frames, ignore_index=True), windows=self._windows)
        in_window = (daily["date"] >= start_date) & (daily["date"] <= end_date)
        return daily.loc[in_window].reset_index(drop=True)

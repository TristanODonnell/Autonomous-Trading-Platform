from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from autonomous_trading_platform.accounting.corporate_actions import (
    SplitSource,
    split_factor_for,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    RAW_BARS_DATASET,
)


class NoBarsForWindow(ValueError):
    """The source dataset holds no bars for the requested symbols and dates (a market
    holiday, an empty fixture day). Callers treat it as "nothing to compute"."""

    def __init__(self, dataset_version_id: str) -> None:
        super().__init__(f"No bar data found for dataset_version_id={dataset_version_id}.")
        self.dataset_version_id = dataset_version_id


@dataclass(slots=True)
class ResolvedSourceDataset:
    dataset_version: DatasetVersion
    frame: pd.DataFrame


class FeatureDatasetResolverService:
    """
    Resolves source datasets required by feature-engineering jobs.

    Responsibilities:
    - Find latest validated source datasets
    - Resolve a specific dataset version when explicitly provided
    - Load source parquet data into a dataframe
    """

    def __init__(
        self,
        dataset_registration_service: DatasetRegistrationService,
        parquet_reader: Any,
        split_source: SplitSource | None = None,
    ) -> None:
        self._dataset_registration_service = dataset_registration_service
        self._parquet_reader = parquet_reader
        # Splits applied to history on read, as the strategy context does (plan 5d, D5).
        self._split_source = split_source

    def get_latest_validated_market_dataset(
        self,
        *,
        price_basis: PriceBasis,
    ) -> DatasetVersion:
        dataset_name = "adjusted_bars" if price_basis == PriceBasis.ADJUSTED else "raw_bars"
        dataset = self._dataset_registration_service.get_latest_validated_dataset(
            dataset_name=dataset_name,
            price_basis=price_basis,
        )
        if dataset is None:
            raise ValueError(
                f"No validated {dataset_name} dataset found for price_basis={price_basis.value}."
            )
        return dataset

    def resolve_source_bars(
        self,
        *,
        price_basis: PriceBasis,
        dataset_version_id: str | None = None,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ResolvedSourceDataset:
        if dataset_version_id is not None:
            dataset_version = self._dataset_registration_service.get_by_dataset_version_id(
                dataset_version_id
            )

            if dataset_version is None:
                raise ValueError(f"Dataset version not found: {dataset_version_id}")

            if dataset_version.price_basis != price_basis:
                raise ValueError(
                    f"Dataset price_basis mismatch: expected {price_basis.value}, "
                    f"got {dataset_version.price_basis.value}"
                )

            if dataset_version.validation_status != "validated":
                raise ValueError(
                    f"Dataset version {dataset_version_id} is not validated: "
                    f"{dataset_version.validation_status}"
                )
        else:
            dataset_version = self.get_latest_validated_market_dataset(
                price_basis=price_basis,
            )

        frame = self.load_bars_frame(
            dataset_version_id=dataset_version.dataset_version_id,
            price_basis=price_basis,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        return ResolvedSourceDataset(
            dataset_version=dataset_version,
            frame=frame,
        )

    def load_bars_frame(
        self,
        *,
        dataset_version_id: str,
        price_basis: PriceBasis,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        if not symbols:
            raise ValueError("symbols must be provided when loading source bars.")

        if start_date is None or end_date is None:
            raise ValueError("start_date and end_date must be provided when loading source bars.")

        frames: list[pd.DataFrame] = []
        parquet_dataset = (
            ADJUSTED_BARS_DATASET if price_basis == PriceBasis.ADJUSTED else RAW_BARS_DATASET
        )

        for symbol in symbols:
            table = self._parquet_reader.read(
                dataset=parquet_dataset,
                dataset_version=dataset_version_id,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

            if table.num_rows > 0:
                frames.append(self._split_adjusted(table.to_pandas(), symbol, start_date, end_date))

        if not frames:
            raise NoBarsForWindow(dataset_version_id)

        return pd.concat(frames, ignore_index=True)

    _PRICE_COLUMNS = ("open", "high", "low", "close", "vwap")

    def _split_adjusted(
        self, frame: pd.DataFrame, symbol: str, start_date: date, end_date: date
    ) -> pd.DataFrame:
        """Express bars before each split's ex-date (ex-date <= end_date) in post-split
        terms so features never see the split jump."""
        if self._split_source is None or frame.empty or "timestamp" not in frame:
            return frame
        splits = self._split_source.splits_for(symbol, start_date, end_date)
        if not splits:
            return frame
        bar_dates = pd.to_datetime(frame["timestamp"]).dt.date
        factors = bar_dates.map(
            lambda d: float(split_factor_for(splits, bar_date=d, as_of=end_date))
        )
        if (factors == 1.0).all():
            return frame
        adjusted = frame.copy()
        for column in self._PRICE_COLUMNS:
            if column in adjusted:
                adjusted[column] = adjusted[column].astype(float) * factors.to_numpy()
        if "volume" in adjusted:
            adjusted["volume"] = (
                (adjusted["volume"].astype(float) / factors.to_numpy()).round().astype("int64")
            )
        if "adjustment_factor" in adjusted:
            adjusted["adjustment_factor"] = (
                adjusted["adjustment_factor"].astype(float) * factors.to_numpy()
            )
        if "price_basis" in adjusted:
            adjusted.loc[factors.to_numpy() != 1.0, "price_basis"] = PriceBasis.ADJUSTED.value
        return adjusted

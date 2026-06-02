from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
)
from autonomous_trading_platform.storage.parquet.datasets import (
    ADJUSTED_BARS_DATASET,
    RAW_BARS_DATASET,
)


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
    ) -> None:
        self._dataset_registration_service = dataset_registration_service
        self._parquet_reader = parquet_reader

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
                frames.append(table.to_pandas())

        if not frames:
            raise ValueError(f"No bar data found for dataset_version_id={dataset_version_id}.")

        return pd.concat(frames, ignore_index=True)

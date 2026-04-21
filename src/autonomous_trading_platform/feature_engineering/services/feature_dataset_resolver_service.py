from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.runtime.services.dataset_registration_service import (
    DatasetRegistrationService,
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
        dataset = self._dataset_registration_service.get_latest_validated_dataset(
            dataset_name="market_bars",
            price_basis=price_basis,
        )
        if dataset is None:
            raise ValueError(
                f"No validated market dataset found for price_basis={price_basis.value}."
            )
        return dataset

    def resolve_source_bars(
        self,
        *,
        price_basis: PriceBasis,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ResolvedSourceDataset:
        dataset_version = self.get_latest_validated_market_dataset(price_basis=price_basis)

        frame = self.load_bars_frame(
            dataset_version_id=dataset_version.dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        resolved = ResolvedSourceDataset(
            dataset_version=dataset_version,
            frame=frame,
        )
        return cast(ResolvedSourceDataset, resolved)

    def load_bars_frame(
        self,
        *,
        dataset_version_id: str,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """
        Replace this with your actual parquet-reading logic.

        Expected output columns might look like:
        - symbol
        - timestamp
        - open
        - high
        - low
        - close
        - volume
        - bid
        - ask
        """
        frame = self._parquet_reader.read_bars(
            dataset_version_id=dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        if frame.empty:
            raise ValueError(f"No bar data found for dataset_version_id={dataset_version_id}.")

        return frame

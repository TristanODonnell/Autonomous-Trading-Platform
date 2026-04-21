from __future__ import annotations

from datetime import date
from typing import Any, cast

from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)


class FeaturePipelineGuardService:
    """
    Prevents unnecessary recomputation of feature datasets.

    Responsibilities:
    - Check whether an equivalent feature dataset already exists
    - Return reusable feature dataset metadata when present
    """

    def __init__(self, feature_dataset_repository: Any) -> None:
        self._feature_dataset_repository = feature_dataset_repository

    def get_existing_feature_dataset(
        self,
        *,
        feature_name: str,
        source_dataset_version_id: str,
        symbols: list[str] | None,
        start_date: date | None,
        end_date: date | None,
        computation_parameters: dict[str, object],
    ) -> FeatureDatasetVersion | None:

        result = self._feature_dataset_repository.find_matching_dataset(
            feature_name=feature_name,
            source_dataset_version_id=source_dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )
        return cast(FeatureDatasetVersion | None, result)

    def feature_exists(
        self,
        *,
        feature_name: str,
        source_dataset_version_id: str,
        symbols: list[str] | None,
        start_date: date | None,
        end_date: date | None,
        computation_parameters: dict[str, object],
    ) -> bool:
        existing = self.get_existing_feature_dataset(
            feature_name=feature_name,
            source_dataset_version_id=source_dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )
        return existing is not None

    def should_run(
        self,
        *,
        feature_name: str,
        source_dataset_version_id: str,
        symbols: list[str] | None,
        start_date: date | None,
        end_date: date | None,
        computation_parameters: dict[str, object],
    ) -> bool:
        return not self.feature_exists(
            feature_name=feature_name,
            source_dataset_version_id=source_dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )

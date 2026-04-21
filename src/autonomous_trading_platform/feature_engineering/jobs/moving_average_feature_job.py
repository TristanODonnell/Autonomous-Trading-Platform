from __future__ import annotations

from datetime import date

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.feature_engineering.services.feature_dataset_resolver_service import (
    FeatureDatasetResolverService,
)
from autonomous_trading_platform.feature_engineering.services.feature_dataset_writer_service import (
    FeatureDatasetWriterService,
)
from autonomous_trading_platform.feature_engineering.services.feature_pipeline_guard_service import (
    FeaturePipelineGuardService,
)
from autonomous_trading_platform.feature_engineering.services.feature_validation_service import (
    FeatureValidationService,
)
from autonomous_trading_platform.feature_engineering.services.moving_average_feature_service import (
    MovingAverageFeatureService,
)


class MovingAverageFeatureJob:
    def __init__(
        self,
        resolver_service: FeatureDatasetResolverService,
        writer_service: FeatureDatasetWriterService,
        guard_service: FeaturePipelineGuardService,
        validation_service: FeatureValidationService,
        moving_average_service: MovingAverageFeatureService,
    ) -> None:
        self._resolver_service = resolver_service
        self._writer_service = writer_service
        self._guard_service = guard_service
        self._validation_service = validation_service
        self._moving_average_service = moving_average_service

    def run(
        self,
        *,
        price_basis: PriceBasis,
        dataset_version_id: str | None = None,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        price_column: str = "close",
        window: int = 20,
    ) -> FeatureDatasetVersion:
        source = self._resolver_service.resolve_source_bars(
            price_basis=price_basis,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        output_column = f"sma_{window}"
        computation_parameters: dict[str, object] = {
            "price_column": price_column,
            "window": window,
            "output_column": output_column,
        }

        existing = self._guard_service.get_existing_feature_dataset(
            feature_name="moving_average",
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )
        if existing is not None:
            return existing

        feature_frame = self._moving_average_service.compute(
            source.frame,
            price_column=price_column,
            window=window,
            output_column=output_column,
        )

        self._validation_service.validate_moving_average(
            feature_frame,
            column_name=output_column,
        )
        self._validation_service.validate_no_nans(
            feature_frame,
            columns=[output_column],
            allow_warmup_nans=True,
        )

        saved = self._writer_service.write_feature_dataset(
            feature_name="moving_average",
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            source_price_basis=price_basis,
            frame=feature_frame,
            computation_parameters=computation_parameters,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        return self._writer_service.mark_validated(feature_dataset_version=saved)

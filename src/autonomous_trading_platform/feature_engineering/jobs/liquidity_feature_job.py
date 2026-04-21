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
from autonomous_trading_platform.feature_engineering.services.liquidity_feature_service import (
    LiquidityFeatureService,
)


class LiquidityFeatureJob:
    def __init__(
        self,
        resolver_service: FeatureDatasetResolverService,
        writer_service: FeatureDatasetWriterService,
        guard_service: FeaturePipelineGuardService,
        validation_service: FeatureValidationService,
        liquidity_service: LiquidityFeatureService,
    ) -> None:
        self._resolver_service = resolver_service
        self._writer_service = writer_service
        self._guard_service = guard_service
        self._validation_service = validation_service
        self._liquidity_service = liquidity_service

    def run(
        self,
        *,
        price_basis: PriceBasis,
        dataset_version_id: str | None = None,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        avg_volume_window: int = 20,
        volume_column: str = "volume",
        bid_column: str = "bid",
        ask_column: str = "ask",
    ) -> FeatureDatasetVersion:
        source = self._resolver_service.resolve_source_bars(
            price_basis=price_basis,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        output_columns = [f"avg_volume_{avg_volume_window}"]
        if "bid" in source.frame.columns and "ask" in source.frame.columns:
            output_columns.append("bid_ask_spread")

        computation_parameters: dict[str, object] = {
            "avg_volume_window": avg_volume_window,
            "volume_column": volume_column,
            "bid_column": bid_column,
            "ask_column": ask_column,
            "output_columns": output_columns,
        }

        existing = self._guard_service.get_existing_feature_dataset(
            feature_name="liquidity",
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )
        if existing is not None:
            return existing

        feature_frame = self._liquidity_service.compute(
            source.frame,
            volume_column=volume_column,
            bid_column=bid_column,
            ask_column=ask_column,
            avg_volume_window=avg_volume_window,
        )

        self._validation_service.validate_liquidity(
            feature_frame,
            column_names=output_columns,
        )
        self._validation_service.validate_no_nans(
            feature_frame,
            columns=output_columns,
            allow_warmup_nans=True,
        )

        saved = self._writer_service.write_feature_dataset(
            feature_name="liquidity",
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            source_price_basis=price_basis,
            frame=feature_frame,
            computation_parameters=computation_parameters,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        return self._writer_service.mark_validated(feature_dataset_version=saved)

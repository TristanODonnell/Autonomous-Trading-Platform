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
from autonomous_trading_platform.feature_engineering.services.regime_classification_feature_service import (
    RegimeClassificationFeatureService,
)

_FEATURE_NAME = "regime_classification"

_REGIME_CLASSIFICATION_COLUMNS = [
    "regime_trend",
    "regime_volatility",
    "regime_liquidity",
    "regime_mean_reversion",
    "regime_risk",
]


class RegimeClassificationFeatureJob:
    def __init__(
        self,
        resolver_service: FeatureDatasetResolverService,
        writer_service: FeatureDatasetWriterService,
        guard_service: FeaturePipelineGuardService,
        validation_service: FeatureValidationService,
        regime_classification_service: RegimeClassificationFeatureService,
    ) -> None:
        self._resolver_service = resolver_service
        self._writer_service = writer_service
        self._guard_service = guard_service
        self._validation_service = validation_service
        self._regime_classification_service = regime_classification_service

    def run(
        self,
        *,
        price_basis: PriceBasis,
        dataset_version_id: str | None = None,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        price_column: str = "close",
        volume_column: str = "volume",
        returns_column: str = "ret_1d",
        trend_short_window: int = 50,
        trend_long_window: int = 200,
        vol_window: int = 20,
        liquidity_avg_window: int = 20,
        zscore_window: int = 20,
        high_percentile: float = 80.0,
        low_percentile: float = 20.0,
    ) -> FeatureDatasetVersion:
        source = self._resolver_service.resolve_source_bars(
            dataset_version_id=dataset_version_id,
            price_basis=price_basis,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        computation_parameters: dict[str, object] = {
            "price_column": price_column,
            "volume_column": volume_column,
            "returns_column": returns_column,
            "trend_short_window": trend_short_window,
            "trend_long_window": trend_long_window,
            "vol_window": vol_window,
            "liquidity_avg_window": liquidity_avg_window,
            "zscore_window": zscore_window,
            "high_percentile": high_percentile,
            "low_percentile": low_percentile,
        }

        existing = self._guard_service.get_existing_feature_dataset(
            feature_name=_FEATURE_NAME,
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            computation_parameters=computation_parameters,
        )
        if existing is not None:
            return existing

        feature_frame = self._regime_classification_service.compute(
            source.frame,
            price_column=price_column,
            volume_column=volume_column,
            returns_column=returns_column,
            trend_short_window=trend_short_window,
            trend_long_window=trend_long_window,
            vol_window=vol_window,
            liquidity_avg_window=liquidity_avg_window,
            zscore_window=zscore_window,
            high_percentile=high_percentile,
            low_percentile=low_percentile,
        )

        self._validation_service.validate_regime_classification(feature_frame)
        self._validation_service.validate_no_nans(
            feature_frame,
            columns=_REGIME_CLASSIFICATION_COLUMNS,
            allow_warmup_nans=True,
        )

        saved = self._writer_service.write_feature_dataset(
            feature_name=_FEATURE_NAME,
            source_dataset_version_id=source.dataset_version.dataset_version_id,
            source_price_basis=price_basis,
            frame=feature_frame,
            computation_parameters=computation_parameters,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
        )

        return self._writer_service.mark_validated(feature_dataset_version=saved)

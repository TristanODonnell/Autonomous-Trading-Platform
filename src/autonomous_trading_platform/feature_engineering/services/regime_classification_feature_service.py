from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.regime_classification_service import (
    RegimeClassificationService,
)


class RegimeClassificationFeatureService:
    """
    Thin adapter that maps the feature-job contract onto RegimeClassificationService.

    Accepts a bars frame (and optionally a pre-computed returns frame) and returns a
    frame conforming to FEATURE_REGIME_CLASSIFICATION_SCHEMA (minus the lineage columns
    which are appended by FeatureDatasetWriterService).
    """

    def __init__(
        self,
        classification_service: RegimeClassificationService | None = None,
    ) -> None:
        self._service = classification_service or RegimeClassificationService()

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        returns_frame: pd.DataFrame | None = None,
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
    ) -> pd.DataFrame:
        required = ["symbol", "timestamp"]
        missing = [c for c in required if c not in bars_frame.columns]
        if missing:
            raise ValueError(f"RegimeClassificationFeatureService missing columns: {missing}")

        return self._service.compute(
            bars_frame,
            returns_frame=returns_frame,
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

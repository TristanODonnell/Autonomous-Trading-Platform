from __future__ import annotations

import numpy as np
import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    VolatilityRegime,
)

_HIGH_PERCENTILE = 80.0
_LOW_PERCENTILE = 20.0


class VolatilityRegimeClassifier:
    """
    Classifies volatility regime per bar as high_volatility / normal_volatility / low_volatility.

    Method:
    - Compute rolling realized volatility (std of daily returns).
    - Compute the expanding percentile rank of each bar's volatility within its own symbol
      history.  Expanding percentile avoids look-ahead bias.
    - high_volatility:   percentile > 80
    - low_volatility:    percentile < 20
    - normal_volatility: 20 ≤ percentile ≤ 80

    Thresholds (the realized-vol values that correspond to the 20th / 80th percentile for
    each symbol) are stored per bar for explainability.

    Confidence = distance of the percentile from the nearest boundary (0 = on boundary,
    1 = far from boundary, e.g. percentile=99 → confidence=0.95).
    """

    def classify(
        self,
        frame: pd.DataFrame,
        *,
        returns_column: str = "ret_1d",
        vol_window: int = 20,
        high_percentile: float = _HIGH_PERCENTILE,
        low_percentile: float = _LOW_PERCENTILE,
    ) -> pd.DataFrame:
        required = ["symbol", "timestamp", returns_column]
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"VolatilityRegimeClassifier missing columns: {missing}")

        df = frame.copy().sort_values(["symbol", "timestamp"])

        df["_vol_realized"] = (
            df.groupby("symbol")[returns_column]
            .rolling(window=vol_window, min_periods=vol_window)
            .std()
            .reset_index(level=0, drop=True)
        )

        def _expanding_pct_rank(s: pd.Series) -> pd.Series:
            return s.expanding(min_periods=1).rank(pct=True) * 100

        df["_vol_percentile"] = df.groupby("symbol")["_vol_realized"].transform(_expanding_pct_rank)

        def _threshold_at_pct(s: pd.Series, pct: float) -> pd.Series:
            return s.expanding(min_periods=1).quantile(pct / 100)

        df["_vol_thresh_high"] = df.groupby("symbol")["_vol_realized"].transform(
            lambda s: _threshold_at_pct(s, high_percentile)
        )
        df["_vol_thresh_low"] = df.groupby("symbol")["_vol_realized"].transform(
            lambda s: _threshold_at_pct(s, low_percentile)
        )

        has_data = df["_vol_realized"].notna()
        pct = df["_vol_percentile"]

        regime = pd.Series(None, index=df.index, dtype=object)
        regime[has_data & (pct > high_percentile)] = VolatilityRegime.HIGH_VOLATILITY.value
        regime[has_data & (pct < low_percentile)] = VolatilityRegime.LOW_VOLATILITY.value
        regime[has_data & (pct >= low_percentile) & (pct <= high_percentile)] = (
            VolatilityRegime.NORMAL_VOLATILITY.value
        )

        # Confidence: fraction of the way from the nearest boundary
        dist_from_high = (pct - high_percentile).abs()
        dist_from_low = (pct - low_percentile).abs()
        nearest_boundary_dist = pd.concat([dist_from_high, dist_from_low], axis=1).min(axis=1)
        boundary_range = high_percentile - low_percentile
        confidence = (nearest_boundary_dist / (boundary_range / 2)).clip(0, 1)

        df["regime_volatility"] = regime
        df["regime_volatility_confidence"] = np.where(has_data, confidence, np.nan)
        df["volatility_realized"] = df["_vol_realized"]
        df["volatility_percentile"] = df["_vol_percentile"]
        df["volatility_threshold_high"] = df["_vol_thresh_high"]
        df["volatility_threshold_low"] = df["_vol_thresh_low"]

        output_cols = [
            "symbol",
            "timestamp",
            "regime_volatility",
            "regime_volatility_confidence",
            "volatility_realized",
            "volatility_percentile",
            "volatility_threshold_high",
            "volatility_threshold_low",
        ]
        return df[output_cols].reset_index(drop=True)

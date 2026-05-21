from __future__ import annotations

import numpy as np
import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    LiquidityRegime,
)

_HIGH_PERCENTILE = 80.0
_LOW_PERCENTILE = 20.0


class LiquidityRegimeClassifier:
    """
    Classifies liquidity regime per bar as high_liquidity / normal_liquidity / low_liquidity.

    Method:
    - Compute rolling average dollar volume (close × volume).
    - Compute expanding percentile rank within each symbol's own history.
    - high_liquidity:   percentile > 80
    - low_liquidity:    percentile < 20
    - normal_liquidity: 20 ≤ percentile ≤ 80

    Dollar volume captures both price and volume dimensions of liquidity.
    """

    def classify(
        self,
        frame: pd.DataFrame,
        *,
        price_column: str = "close",
        volume_column: str = "volume",
        avg_window: int = 20,
        high_percentile: float = _HIGH_PERCENTILE,
        low_percentile: float = _LOW_PERCENTILE,
    ) -> pd.DataFrame:
        required = ["symbol", "timestamp", price_column, volume_column]
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"LiquidityRegimeClassifier missing columns: {missing}")

        df = frame.copy().sort_values(["symbol", "timestamp"])

        df["_dollar_volume"] = df[price_column] * df[volume_column].astype(float)

        df["_liq_avg_dv"] = (
            df.groupby("symbol")["_dollar_volume"]
            .rolling(window=avg_window, min_periods=avg_window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        def _expanding_pct_rank(s: pd.Series) -> pd.Series:
            return s.expanding(min_periods=1).rank(pct=True) * 100

        df["_liq_percentile"] = df.groupby("symbol")["_liq_avg_dv"].transform(_expanding_pct_rank)

        has_data = df["_liq_avg_dv"].notna()
        pct = df["_liq_percentile"]

        regime = pd.Series(None, index=df.index, dtype=object)
        regime[has_data & (pct > high_percentile)] = LiquidityRegime.HIGH_LIQUIDITY.value
        regime[has_data & (pct < low_percentile)] = LiquidityRegime.LOW_LIQUIDITY.value
        regime[has_data & (pct >= low_percentile) & (pct <= high_percentile)] = (
            LiquidityRegime.NORMAL_LIQUIDITY.value
        )

        dist_from_high = (pct - high_percentile).abs()
        dist_from_low = (pct - low_percentile).abs()
        nearest_boundary_dist = pd.concat([dist_from_high, dist_from_low], axis=1).min(axis=1)
        boundary_range = high_percentile - low_percentile
        confidence = (nearest_boundary_dist / (boundary_range / 2)).clip(0, 1)

        df["regime_liquidity"] = regime
        df["regime_liquidity_confidence"] = np.where(has_data, confidence, np.nan)
        df["liquidity_avg_dollar_volume"] = df["_liq_avg_dv"]
        df["liquidity_percentile"] = df["_liq_percentile"]

        output_cols = [
            "symbol",
            "timestamp",
            "regime_liquidity",
            "regime_liquidity_confidence",
            "liquidity_avg_dollar_volume",
            "liquidity_percentile",
        ]
        return df[output_cols].reset_index(drop=True)

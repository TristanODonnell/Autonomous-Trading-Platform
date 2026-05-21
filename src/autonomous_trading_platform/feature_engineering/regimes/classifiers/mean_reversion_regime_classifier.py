from __future__ import annotations

import numpy as np
import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    MeanReversionRegime,
)


class MeanReversionRegimeClassifier:
    """
    Classifies mean-reversion regime per bar as trending / mean_reverting / undefined.

    Method uses two orthogonal signals, both of which must agree for a confident label:

    1. zscore_std: rolling std dev of the z-score series.
       - A mean-reverting process oscillates near 0 → low zscore_std.
       - A trending process drifts far from mean → high zscore_std.
       Classification boundary: expanding median of zscore_std per symbol.

    2. trend_strength: |rolling_return_window| / realized_vol.
       Normalized directional movement.  High = trending; low = mean-reverting.
       Boundary: 0.5 (half a vol unit of net return over the window).

    Label logic:
    - trending:       zscore_std > median AND trend_strength > boundary
    - mean_reverting: zscore_std ≤ median AND trend_strength ≤ boundary
    - undefined:      mixed signals

    Confidence = geometric mean of the two individual signal confidences.
    """

    def classify(
        self,
        frame: pd.DataFrame,
        *,
        price_column: str = "close",
        returns_column: str = "ret_1d",
        zscore_window: int = 20,
        vol_window: int = 20,
        return_window: int = 20,
        trend_strength_boundary: float = 0.5,
    ) -> pd.DataFrame:
        required = ["symbol", "timestamp", price_column, returns_column]
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"MeanReversionRegimeClassifier missing columns: {missing}")

        df = frame.copy().sort_values(["symbol", "timestamp"])

        grp_price = df.groupby("symbol")[price_column]
        grp_ret = df.groupby("symbol")[returns_column]

        rolling_mean = (
            grp_price.rolling(window=zscore_window, min_periods=zscore_window)
            .mean()
            .reset_index(level=0, drop=True)
        )
        rolling_std = (
            grp_price.rolling(window=zscore_window, min_periods=zscore_window)
            .std()
            .reset_index(level=0, drop=True)
        )
        zscore = (df[price_column] - rolling_mean) / rolling_std.replace(0, np.nan)

        df["_zscore"] = zscore

        df["_zscore_std"] = (
            df.groupby("symbol")["_zscore"]
            .rolling(window=zscore_window, min_periods=zscore_window)
            .std()
            .reset_index(level=0, drop=True)
        )

        realized_vol = (
            grp_ret.rolling(window=vol_window, min_periods=vol_window)
            .std()
            .reset_index(level=0, drop=True)
        )
        rolling_return = grp_price.pct_change(periods=return_window).reset_index(level=0, drop=True)
        df["_trend_strength"] = rolling_return.abs() / realized_vol.replace(0, np.nan)

        def _expanding_median(s: pd.Series) -> pd.Series:
            return s.expanding(min_periods=1).median()

        df["_zscore_std_median"] = df.groupby("symbol")["_zscore_std"].transform(_expanding_median)

        has_data = df["_zscore_std"].notna() & df["_trend_strength"].notna()

        zs_high = df["_zscore_std"] > df["_zscore_std_median"]
        ts_high = df["_trend_strength"] > trend_strength_boundary

        regime = pd.Series(None, index=df.index, dtype=object)
        regime[has_data & zs_high & ts_high] = MeanReversionRegime.TRENDING.value
        regime[has_data & ~zs_high & ~ts_high] = MeanReversionRegime.MEAN_REVERTING.value
        regime[has_data & (zs_high != ts_high)] = MeanReversionRegime.UNDEFINED.value

        # Confidence: how far from each boundary, combined
        zscore_std_gap = (
            (df["_zscore_std"] - df["_zscore_std_median"]).abs()
            / (df["_zscore_std_median"].abs() + 1e-10)
        ).clip(0, 1)
        ts_gap = (df["_trend_strength"] - trend_strength_boundary).abs().clip(0, 2) / 2
        confidence = np.sqrt(zscore_std_gap * ts_gap)

        df["regime_mean_reversion"] = regime
        df["regime_mean_reversion_confidence"] = np.where(has_data, confidence, np.nan)
        df["mean_reversion_zscore_std"] = df["_zscore_std"]
        df["mean_reversion_trend_strength"] = df["_trend_strength"]

        output_cols = [
            "symbol",
            "timestamp",
            "regime_mean_reversion",
            "regime_mean_reversion_confidence",
            "mean_reversion_zscore_std",
            "mean_reversion_trend_strength",
        ]
        return df[output_cols].reset_index(drop=True)

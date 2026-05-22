from __future__ import annotations

import numpy as np
import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    TrendRegime,
)


class TrendRegimeClassifier:
    """
    Classifies trend regime per bar as bull / bear / sideways.

    Rules (all NaN-safe; warmup bars yield None regime):
    - bull:     short_ma > long_ma AND rolling_return_20d >= 0
    - bear:     short_ma < long_ma AND rolling_return_20d < 0
    - sideways: MA relationship and return disagree, or MA difference is too small

    Confidence = normalized MA spread clamped to [0, 1].
    """

    def classify(
        self,
        frame: pd.DataFrame,
        *,
        price_column: str = "close",
        short_window: int = 50,
        long_window: int = 200,
        return_window: int = 20,
    ) -> pd.DataFrame:
        required = ["symbol", "timestamp", price_column]
        missing = [c for c in required if c not in frame.columns]
        if missing:
            raise ValueError(f"TrendRegimeClassifier missing columns: {missing}")

        df = frame.copy().sort_values(["symbol", "timestamp"])

        grp = df.groupby("symbol")[price_column]

        df["_trend_ma_short"] = (
            grp.rolling(window=short_window, min_periods=short_window)
            .mean()
            .reset_index(level=0, drop=True)
        )
        df["_trend_ma_long"] = (
            grp.rolling(window=long_window, min_periods=long_window)
            .mean()
            .reset_index(level=0, drop=True)
        )
        df["_trend_return_20d"] = grp.pct_change(periods=return_window).reset_index(
            level=0, drop=True
        )

        spread = (df["_trend_ma_short"] - df["_trend_ma_long"]) / df["_trend_ma_long"].abs()
        confidence = spread.abs().clip(0, 0.05) / 0.05  # saturates at 5% spread

        regime = pd.Series("sideways", index=df.index, dtype=object)
        bull_mask = (df["_trend_ma_short"] > df["_trend_ma_long"]) & (df["_trend_return_20d"] >= 0)
        bear_mask = (df["_trend_ma_short"] < df["_trend_ma_long"]) & (df["_trend_return_20d"] < 0)

        # Warmup: either MA is NaN → regime stays None
        has_data = df["_trend_ma_short"].notna() & df["_trend_ma_long"].notna()
        regime[~has_data] = None
        regime[has_data & bull_mask] = TrendRegime.BULL.value
        regime[has_data & bear_mask] = TrendRegime.BEAR.value
        # sideways: has_data but neither bull nor bear (including return-MA disagreement)

        df["regime_trend"] = regime
        df["regime_trend_confidence"] = np.where(has_data, confidence, np.nan)
        df["trend_ma_short"] = df["_trend_ma_short"]
        df["trend_ma_long"] = df["_trend_ma_long"]
        df["trend_rolling_return_20d"] = df["_trend_return_20d"]

        output_cols = [
            "symbol",
            "timestamp",
            "regime_trend",
            "regime_trend_confidence",
            "trend_ma_short",
            "trend_ma_long",
            "trend_rolling_return_20d",
        ]
        return df[output_cols].reset_index(drop=True)

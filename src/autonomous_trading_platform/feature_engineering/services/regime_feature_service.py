from __future__ import annotations

import pandas as pd


class RegimeFeatureService:
    """
    Computes simple regime labels such as bull / bear / sideways.

    This is intentionally basic for the initial version.
    """

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        price_column: str = "close",
        short_window: int = 50,
        long_window: int = 200,
        output_column: str = "regime",
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", price_column]
        missing = [column for column in required_columns if column not in bars_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for regime computation: {missing}")

        frame = bars_frame.copy()
        frame = frame.sort_values(["symbol", "timestamp"])

        short_ma = (
            frame.groupby("symbol")[price_column]
            .rolling(window=short_window, min_periods=short_window)
            .mean()
            .reset_index(level=0, drop=True)
        )
        long_ma = (
            frame.groupby("symbol")[price_column]
            .rolling(window=long_window, min_periods=long_window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        frame["short_ma"] = short_ma
        frame["long_ma"] = long_ma
        frame[output_column] = "sideways"
        frame.loc[frame["short_ma"] > frame["long_ma"], output_column] = "bull"
        frame.loc[frame["short_ma"] < frame["long_ma"], output_column] = "bear"

        return frame[["symbol", "timestamp", output_column]]

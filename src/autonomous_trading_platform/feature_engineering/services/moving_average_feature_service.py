from __future__ import annotations

import pandas as pd


class MovingAverageFeatureService:
    """
    Computes moving-average features from bar prices.
    """

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        price_column: str = "close",
        window: int = 20,
        output_column: str | None = None,
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", price_column]
        missing = [column for column in required_columns if column not in bars_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for moving-average computation: {missing}")

        output_column = output_column or f"sma_{window}"

        frame = bars_frame.copy()
        frame = frame.sort_values(["symbol", "timestamp"])

        frame[output_column] = (
            frame.groupby("symbol")[price_column]
            .rolling(window=window, min_periods=window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        return frame[["symbol", "timestamp", output_column]]

from __future__ import annotations

import pandas as pd


class ReturnsFeatureService:
    """
    Computes return-based features from bar data.
    """

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        price_column: str = "close",
        output_column: str = "returns",
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", price_column]
        missing = [column for column in required_columns if column not in bars_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for returns computation: {missing}")

        frame = bars_frame.copy()
        frame = frame.sort_values(["symbol", "timestamp"])

        frame[output_column] = frame.groupby("symbol")[price_column].pct_change()

        return frame[["symbol", "timestamp", output_column]]

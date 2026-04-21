from __future__ import annotations

import pandas as pd


class VolatilityFeatureService:
    """
    Computes rolling volatility features from returns.
    """

    def compute(
        self,
        returns_frame: pd.DataFrame,
        *,
        returns_column: str = "returns",
        window: int = 20,
        output_column: str | None = None,
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", returns_column]
        missing = [column for column in required_columns if column not in returns_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for volatility computation: {missing}")

        output_column = output_column or f"volatility_{window}"

        frame = returns_frame.copy()
        frame = frame.sort_values(["symbol", "timestamp"])

        frame[output_column] = (
            frame.groupby("symbol")[returns_column]
            .rolling(window=window, min_periods=window)
            .std()
            .reset_index(level=0, drop=True)
        )

        return frame[["symbol", "timestamp", output_column]]

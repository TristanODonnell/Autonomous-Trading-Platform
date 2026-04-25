from __future__ import annotations

import pandas as pd


class LiquidityFeatureService:
    """
    Computes liquidity-oriented feature columns such as rolling average volume
    and optional spread proxies.
    """

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        volume_column: str = "volume",
        bid_column: str = "bid",
        ask_column: str = "ask",
        avg_volume_window: int = 20,
        avg_volume_output_column: str = "avg_volume_value",
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", volume_column]
        missing = [column for column in required_columns if column not in bars_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for liquidity computation: {missing}")

        frame = bars_frame.copy()
        frame = frame.sort_values(["symbol", "timestamp"])

        frame[avg_volume_output_column] = (
            frame.groupby("symbol")[volume_column]
            .rolling(window=avg_volume_window, min_periods=avg_volume_window)
            .mean()
            .reset_index(level=0, drop=True)
        )

        output_columns = ["symbol", "timestamp", avg_volume_output_column]

        has_bid_ask = bid_column in frame.columns and ask_column in frame.columns

        if has_bid_ask:
            frame["bid_ask_spread"] = frame[ask_column] - frame[bid_column]
        else:
            frame["bid_ask_spread"] = pd.NA

        output_columns = [
            "symbol",
            "timestamp",
            avg_volume_output_column,
            "bid_ask_spread",
        ]

        return frame[output_columns]

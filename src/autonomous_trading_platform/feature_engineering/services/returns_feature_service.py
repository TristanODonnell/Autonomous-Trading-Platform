from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.contracts.common.enums import PriceBasis


class ReturnsFeatureService:
    """
    Computes return-based features from bar data.
    """

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        price_column: str = "close",
        source_dataset_version_id: str,
        price_basis: PriceBasis,
    ) -> pd.DataFrame:
        required_columns = ["symbol", "timestamp", price_column]
        missing = [column for column in required_columns if column not in bars_frame.columns]
        if missing:
            raise ValueError(f"Missing required columns for returns computation: {missing}")

        frame = bars_frame.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        frame = frame.sort_values(["symbol", "timestamp"])

        grouped_prices = frame.groupby("symbol")[price_column]

        frame["ret_1d"] = grouped_prices.pct_change(1)
        frame["ret_5d"] = grouped_prices.pct_change(5)
        frame["ret_20d"] = grouped_prices.pct_change(20)

        frame["date"] = frame["timestamp"].dt.date
        frame["underlying_dataset_version"] = source_dataset_version_id
        frame["price_basis"] = price_basis.value
        frame["year"] = frame["timestamp"].dt.year
        frame["month"] = frame["timestamp"].dt.month

        return frame[
            [
                "symbol",
                "timestamp",
                "date",
                "ret_1d",
                "ret_5d",
                "ret_20d",
                "underlying_dataset_version",
                "price_basis",
                "year",
                "month",
            ]
        ]

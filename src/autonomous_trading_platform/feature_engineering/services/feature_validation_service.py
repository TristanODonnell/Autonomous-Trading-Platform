from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


class FeatureValidationService:
    """
    Central validation checks for computed feature datasets.
    """

    def validate_required_columns(
        self,
        frame: pd.DataFrame,
        *,
        required_columns: Iterable[str],
    ) -> None:
        missing = [column for column in required_columns if column not in frame.columns]
        if missing:
            raise ValueError(f"Missing required feature columns: {missing}")

    def validate_not_empty(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            raise ValueError("Computed feature dataframe is empty.")

    def validate_no_nans(
        self,
        frame: pd.DataFrame,
        *,
        columns: Iterable[str],
        allow_warmup_nans: bool = False,
    ) -> None:
        if allow_warmup_nans:
            return

        nan_columns = [column for column in columns if frame[column].isna().any()]
        if nan_columns:
            raise ValueError(f"NaN values found in feature columns: {nan_columns}")

    def validate_numeric_range(
        self,
        frame: pd.DataFrame,
        *,
        column: str,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        if column not in frame.columns:
            raise ValueError(f"Column not found for range validation: {column}")

        series = frame[column].dropna()
        if minimum is not None and (series < minimum).any():
            raise ValueError(f"Column {column} contains values below minimum={minimum}")
        if maximum is not None and (series > maximum).any():
            raise ValueError(f"Column {column} contains values above maximum={maximum}")

    def validate_returns(self, frame: pd.DataFrame) -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=[
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
            ],
        )

    def validate_volatility(self, frame: pd.DataFrame, *, column_name: str) -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=["symbol", "timestamp", column_name],
        )
        self.validate_numeric_range(frame, column=column_name, minimum=0.0)

    def validate_moving_average(self, frame: pd.DataFrame, *, column_name: str) -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=["symbol", "timestamp", column_name],
        )

    def validate_liquidity(self, frame: pd.DataFrame, *, column_names: list[str]) -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=["symbol", "timestamp", *column_names],
        )

    def validate_regime(self, frame: pd.DataFrame, *, column_name: str = "regime") -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=["symbol", "timestamp", column_name],
        )

    def validate_regime_classification(self, frame: pd.DataFrame) -> None:
        self.validate_not_empty(frame)
        self.validate_required_columns(
            frame,
            required_columns=[
                "symbol",
                "timestamp",
                "regime_trend",
                "regime_volatility",
                "regime_liquidity",
                "regime_mean_reversion",
                "regime_risk",
            ],
        )

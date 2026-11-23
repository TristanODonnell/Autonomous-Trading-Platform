from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.research.analysis.regimes.regime_bucket import REGIME_COLUMN_MAP

_REGIME_COLS = list(REGIME_COLUMN_MAP.values())


def _regime_cols_in(df: pd.DataFrame) -> list[str]:
    return [c for c in _REGIME_COLS if c in df.columns]


class RegimeJoinService:
    """
    Joins persisted regime classification data onto simulation output DataFrames.

    All joins are exact on timestamp (and symbol where applicable).  No
    forward-fill or backward-fill is applied.  Bars without a matching regime
    row receive NaN regime columns and are excluded from regime-conditioned
    metric computations downstream.

    This is intentional: silently forward-filling future labels would introduce
    look-ahead bias, and backward-filling would attribute future regime labels
    to past bars incorrectly.
    """

    def join_equity_curve(
        self,
        equity_curve: pd.DataFrame,
        regime_data: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Join regime labels onto portfolio-level equity curve.

        Aggregates across symbols at each timestamp by taking the modal
        (most common) regime label.  Single-symbol simulations produce an
        exact join.
        """
        if regime_data.empty:
            return equity_curve.copy()

        available = _regime_cols_in(regime_data)
        if not available:
            return equity_curve.copy()

        labels = regime_data[["timestamp"] + available]
        if labels["timestamp"].is_unique:
            regime_by_ts = self._one_row_per_timestamp(labels, available)
        else:
            regime_by_ts = self._modal_by_timestamp(labels)

        return equity_curve.merge(regime_by_ts, on="timestamp", how="left")

    @staticmethod
    def _modal_by_timestamp(labels: pd.DataFrame) -> pd.DataFrame:
        """The most common label at each timestamp, None where every row lacks one."""
        return (
            labels.groupby("timestamp", sort=False)
            .agg(lambda x: x.mode().iloc[0] if len(x) > 0 and x.notna().any() else None)
            .reset_index()
        )

    @staticmethod
    def _one_row_per_timestamp(labels: pd.DataFrame, available: list[str]) -> pd.DataFrame:
        """The same answer as _modal_by_timestamp when no timestamp repeats.

        The most common label of a single row is that row's label, so nothing
        has to be aggregated. The research pipeline always lands here: its
        labels are built on the equity curve's own timestamps, one row each.
        The aggregation calls a Python function per timestamp per column, which
        took about 13 seconds for a 60-session five-minute curve (4,681 rows).
        """
        frame = labels[labels["timestamp"].notna()].reset_index(drop=True)
        for column in available:
            values = frame[column].astype(object)
            frame[column] = values.where(values.notna(), None)
        return frame

    def join_symbol_frame(
        self,
        frame: pd.DataFrame,
        regime_data: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Join regime labels onto a symbol-level frame (trade_logs, per_bar_metrics).

        Exact join on (symbol, timestamp).
        """
        if frame.empty or regime_data.empty:
            return frame.copy()

        available = _regime_cols_in(regime_data)
        if not available:
            return frame.copy()

        regime_subset = regime_data[["symbol", "timestamp"] + available].copy()
        return frame.merge(regime_subset, on=["symbol", "timestamp"], how="left")

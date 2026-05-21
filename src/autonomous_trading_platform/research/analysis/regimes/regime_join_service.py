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

        regime_by_ts = (
            regime_data[["timestamp"] + available]
            .groupby("timestamp", sort=False)
            .agg(lambda x: x.mode().iloc[0] if len(x) > 0 and x.notna().any() else None)
            .reset_index()
        )

        return equity_curve.merge(regime_by_ts, on="timestamp", how="left")

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

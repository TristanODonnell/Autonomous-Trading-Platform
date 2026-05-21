from __future__ import annotations

import pandas as pd

from autonomous_trading_platform.feature_engineering.regimes.classifiers.liquidity_regime_classifier import (
    LiquidityRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.classifiers.mean_reversion_regime_classifier import (
    MeanReversionRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.classifiers.trend_regime_classifier import (
    TrendRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.classifiers.volatility_regime_classifier import (
    VolatilityRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    RiskRegime,
    TrendRegime,
    VolatilityRegime,
)


def _derive_risk_regime(trend: pd.Series, volatility: pd.Series) -> pd.Series:
    risk = pd.Series(None, index=trend.index, dtype=object)
    has_data = trend.notna() & volatility.notna()
    risk_on = (
        has_data
        & (trend == TrendRegime.BULL.value)
        & (volatility != VolatilityRegime.HIGH_VOLATILITY.value)
    )
    risk_off = (
        has_data
        & (trend == TrendRegime.BEAR.value)
        & (volatility == VolatilityRegime.HIGH_VOLATILITY.value)
    )
    risk[has_data] = RiskRegime.NEUTRAL.value
    risk[risk_on] = RiskRegime.RISK_ON.value
    risk[risk_off] = RiskRegime.RISK_OFF.value
    return risk


class RegimeClassificationService:
    """
    Orchestrates all four regime classifiers to produce a single multi-dimensional
    classification frame.

    Input frame must contain at minimum: symbol, timestamp, close, volume, ret_1d.

    Output frame columns (all nullable during warmup):
      regime_trend, regime_trend_confidence, trend_ma_short, trend_ma_long,
      trend_rolling_return_20d, regime_volatility, regime_volatility_confidence,
      volatility_realized, volatility_percentile, volatility_threshold_high,
      volatility_threshold_low, regime_liquidity, regime_liquidity_confidence,
      liquidity_avg_dollar_volume, liquidity_percentile, regime_mean_reversion,
      regime_mean_reversion_confidence, mean_reversion_zscore_std,
      mean_reversion_trend_strength, regime_risk
    """

    def __init__(
        self,
        trend_classifier: TrendRegimeClassifier | None = None,
        volatility_classifier: VolatilityRegimeClassifier | None = None,
        liquidity_classifier: LiquidityRegimeClassifier | None = None,
        mean_reversion_classifier: MeanReversionRegimeClassifier | None = None,
    ) -> None:
        self._trend = trend_classifier or TrendRegimeClassifier()
        self._volatility = volatility_classifier or VolatilityRegimeClassifier()
        self._liquidity = liquidity_classifier or LiquidityRegimeClassifier()
        self._mean_reversion = mean_reversion_classifier or MeanReversionRegimeClassifier()

    def compute(
        self,
        bars_frame: pd.DataFrame,
        *,
        returns_frame: pd.DataFrame | None = None,
        price_column: str = "close",
        volume_column: str = "volume",
        returns_column: str = "ret_1d",
        trend_short_window: int = 50,
        trend_long_window: int = 200,
        vol_window: int = 20,
        liquidity_avg_window: int = 20,
        zscore_window: int = 20,
        high_percentile: float = 80.0,
        low_percentile: float = 20.0,
    ) -> pd.DataFrame:
        """
        Compute all regime dimensions for every bar.

        If returns_frame is provided it must have columns [symbol, timestamp, ret_1d]
        and will be joined onto bars_frame for volatility and mean-reversion signals.
        Otherwise a 1-period percent-change is computed inline from bars_frame.
        """
        required = ["symbol", "timestamp", price_column, volume_column]
        missing = [c for c in required if c not in bars_frame.columns]
        if missing:
            raise ValueError(f"RegimeClassificationService missing columns: {missing}")

        df = bars_frame.copy().sort_values(["symbol", "timestamp"])

        if returns_frame is not None:
            ret_cols = ["symbol", "timestamp", returns_column]
            missing_ret = [c for c in ret_cols if c not in returns_frame.columns]
            if missing_ret:
                raise ValueError(
                    f"RegimeClassificationService: returns_frame missing columns: {missing_ret}"
                )
            ret = returns_frame[ret_cols].copy()
            df = df.merge(ret, on=["symbol", "timestamp"], how="left")
        else:
            df[returns_column] = (
                df.groupby("symbol")[price_column]
                .pct_change(periods=1)
                .reset_index(level=0, drop=True)
            )

        trend_df = self._trend.classify(
            df,
            price_column=price_column,
            short_window=trend_short_window,
            long_window=trend_long_window,
            return_window=20,
        )

        vol_df = self._volatility.classify(
            df,
            returns_column=returns_column,
            vol_window=vol_window,
            high_percentile=high_percentile,
            low_percentile=low_percentile,
        )

        liq_df = self._liquidity.classify(
            df,
            price_column=price_column,
            volume_column=volume_column,
            avg_window=liquidity_avg_window,
            high_percentile=high_percentile,
            low_percentile=low_percentile,
        )

        mr_df = self._mean_reversion.classify(
            df,
            price_column=price_column,
            returns_column=returns_column,
            zscore_window=zscore_window,
            vol_window=vol_window,
            return_window=20,
        )

        result = (
            trend_df.merge(vol_df, on=["symbol", "timestamp"], how="left")
            .merge(liq_df, on=["symbol", "timestamp"], how="left")
            .merge(mr_df, on=["symbol", "timestamp"], how="left")
        )

        result["regime_risk"] = _derive_risk_regime(
            result["regime_trend"], result["regime_volatility"]
        )

        return result

"""Tests for RegimeClassificationService (orchestrator)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.regimes.regime_classification_service import (
    RegimeClassificationService,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import (
    RiskRegime,
    TrendRegime,
    VolatilityRegime,
)


def _make_bars(n: int = 300, *, uptrend: bool = True, symbol: str = "AAPL") -> pd.DataFrame:
    rng = np.random.default_rng(42)
    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    if uptrend:
        prices = [100.0 + i * 0.4 + rng.normal(0, 0.5) for i in range(n)]
    else:
        prices = [250.0 - i * 0.4 + rng.normal(0, 0.5) for i in range(n)]
    volumes = [int(rng.integers(500_000, 2_000_000)) for _ in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices, "volume": volumes})


_ALL_REGIME_COLUMNS = [
    "regime_trend",
    "regime_trend_confidence",
    "trend_ma_short",
    "trend_ma_long",
    "trend_rolling_return_20d",
    "regime_volatility",
    "regime_volatility_confidence",
    "volatility_realized",
    "volatility_percentile",
    "volatility_threshold_high",
    "volatility_threshold_low",
    "regime_liquidity",
    "regime_liquidity_confidence",
    "liquidity_avg_dollar_volume",
    "liquidity_percentile",
    "regime_mean_reversion",
    "regime_mean_reversion_confidence",
    "mean_reversion_zscore_std",
    "mean_reversion_trend_strength",
    "regime_risk",
]


class TestRegimeClassificationService:
    def setup_method(self):
        self.service = RegimeClassificationService()

    def test_all_output_columns_present(self):
        df = _make_bars()
        result = self.service.compute(df)
        assert "symbol" in result.columns
        assert "timestamp" in result.columns
        for col in _ALL_REGIME_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_row_count_matches_input(self):
        df = _make_bars(300)
        result = self.service.compute(df)
        assert len(result) == len(df)

    def test_deterministic(self):
        df = _make_bars(300)
        r1 = self.service.compute(df)
        r2 = self.service.compute(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_risk_on_during_bull_normal_vol(self):
        """risk_on should appear when trend=bull and vol is not high."""
        df = _make_bars(300, uptrend=True)
        result = self.service.compute(df).dropna(subset=["regime_trend", "regime_volatility"])
        bull_normal = result[
            (result["regime_trend"] == TrendRegime.BULL.value)
            & (result["regime_volatility"] == VolatilityRegime.NORMAL_VOLATILITY.value)
        ]
        if len(bull_normal) > 0:
            assert (bull_normal["regime_risk"] == RiskRegime.RISK_ON.value).all()

    def test_risk_off_during_bear_high_vol(self):
        """risk_off should appear when trend=bear and vol=high_volatility."""
        df = _make_bars(300, uptrend=False)
        result = self.service.compute(df).dropna(subset=["regime_trend", "regime_volatility"])
        bear_high = result[
            (result["regime_trend"] == TrendRegime.BEAR.value)
            & (result["regime_volatility"] == VolatilityRegime.HIGH_VOLATILITY.value)
        ]
        if len(bear_high) > 0:
            assert (bear_high["regime_risk"] == RiskRegime.RISK_OFF.value).all()

    def test_inline_returns_when_no_returns_frame(self):
        """Service computes ret_1d inline when returns_frame is not provided."""
        df = _make_bars(300)
        result = self.service.compute(df, returns_frame=None)
        assert len(result) == len(df)

    def test_external_returns_frame_accepted(self):
        df = _make_bars(300)
        ret_df = df[["symbol", "timestamp"]].copy()
        ret_df["ret_1d"] = df["close"].pct_change().values
        result = self.service.compute(df, returns_frame=ret_df)
        assert len(result) == len(df)

    def test_missing_bars_column_raises(self):
        df = _make_bars(300).drop(columns=["volume"])
        with pytest.raises(ValueError, match="missing columns"):
            self.service.compute(df)

    def test_multi_symbol(self):
        aapl = _make_bars(300, uptrend=True, symbol="AAPL")
        msft = _make_bars(300, uptrend=False, symbol="MSFT")
        combined = pd.concat([aapl, msft], ignore_index=True)
        result = self.service.compute(combined)
        assert set(result["symbol"].unique()) == {"AAPL", "MSFT"}
        assert len(result) == len(combined)

"""Tests for RegimeClassificationFeatureService (feature-job adapter)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.services.regime_classification_feature_service import (
    RegimeClassificationFeatureService,
)


def _make_bars(n: int = 300, symbol: str = "AAPL") -> pd.DataFrame:
    rng = np.random.default_rng(55)
    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    prices = [100.0 + i * 0.3 + rng.normal(0, 0.3) for i in range(n)]
    volumes = [int(rng.integers(500_000, 2_000_000)) for _ in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices, "volume": volumes})


class TestRegimeClassificationFeatureService:
    def setup_method(self):
        self.service = RegimeClassificationFeatureService()

    def test_returns_dataframe(self):
        df = _make_bars()
        result = self.service.compute(df)
        assert isinstance(result, pd.DataFrame)

    def test_output_has_required_columns(self):
        df = _make_bars()
        result = self.service.compute(df)
        assert "regime_trend" in result.columns
        assert "regime_volatility" in result.columns
        assert "regime_liquidity" in result.columns
        assert "regime_mean_reversion" in result.columns
        assert "regime_risk" in result.columns

    def test_row_count_preserved(self):
        df = _make_bars(300)
        result = self.service.compute(df)
        assert len(result) == 300

    def test_deterministic(self):
        df = _make_bars(300)
        r1 = self.service.compute(df)
        r2 = self.service.compute(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_missing_symbol_raises(self):
        df = _make_bars(50).drop(columns=["symbol"])
        with pytest.raises(ValueError, match="missing columns"):
            self.service.compute(df)

    def test_custom_parameters_forwarded(self):
        df = _make_bars(300)
        result = self.service.compute(df, trend_short_window=20, trend_long_window=50)
        assert len(result) == 300

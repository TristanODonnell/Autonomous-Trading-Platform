"""Tests for LiquidityRegimeClassifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.regimes.classifiers.liquidity_regime_classifier import (
    LiquidityRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import LiquidityRegime


def _make_bars(
    prices: list[float],
    volumes: list[int],
    symbol: str = "AAPL",
) -> pd.DataFrame:
    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices, "volume": volumes})


class TestLiquidityRegimeClassifier:
    def setup_method(self):
        self.classifier = LiquidityRegimeClassifier()

    def test_output_columns_present(self):
        prices = [100.0] * 100
        volumes = [1_000_000] * 100
        df = _make_bars(prices, volumes)
        result = self.classifier.classify(df)
        expected = {
            "symbol",
            "timestamp",
            "regime_liquidity",
            "regime_liquidity_confidence",
            "liquidity_avg_dollar_volume",
            "liquidity_percentile",
        }
        assert expected.issubset(set(result.columns))

    def test_warmup_bars_null(self):
        prices = [100.0] * 100
        volumes = [1_000_000] * 100
        df = _make_bars(prices, volumes)
        result = self.classifier.classify(df, avg_window=20)
        assert result["regime_liquidity"].head(19).isna().all()

    def test_high_liquidity_on_large_volume(self):
        rng = np.random.default_rng(1)
        prices = list(rng.uniform(100, 110, 200).tolist())
        low_vol = [100_000] * 150
        high_vol = [10_000_000] * 50
        df = _make_bars(prices, low_vol + high_vol)
        result = self.classifier.classify(df, avg_window=20)
        last = result.tail(20).dropna(subset=["regime_liquidity"])
        high_count = (last["regime_liquidity"] == LiquidityRegime.HIGH_LIQUIDITY.value).sum()
        assert high_count > 0

    def test_low_liquidity_on_small_volume(self):
        rng = np.random.default_rng(2)
        prices = list(rng.uniform(100, 110, 200).tolist())
        high_vol = [10_000_000] * 150
        low_vol = [1_000] * 50
        df = _make_bars(prices, high_vol + low_vol)
        result = self.classifier.classify(df, avg_window=20)
        last = result.tail(20).dropna(subset=["regime_liquidity"])
        low_count = (last["regime_liquidity"] == LiquidityRegime.LOW_LIQUIDITY.value).sum()
        assert low_count > 0

    def test_confidence_between_0_and_1(self):
        rng = np.random.default_rng(3)
        prices = list(rng.uniform(100, 110, 200).tolist())
        volumes = list(rng.integers(100_000, 10_000_000, 200).tolist())
        df = _make_bars(prices, volumes)
        result = self.classifier.classify(df)
        valid_conf = result["regime_liquidity_confidence"].dropna()
        assert (valid_conf >= 0).all()
        assert (valid_conf <= 1).all()

    def test_deterministic(self):
        rng = np.random.default_rng(4)
        prices = list(rng.uniform(100, 110, 200).tolist())
        volumes = list(rng.integers(100_000, 10_000_000, 200).tolist())
        df = _make_bars(prices, volumes)
        r1 = self.classifier.classify(df)
        r2 = self.classifier.classify(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_missing_required_column_raises(self):
        prices = [100.0] * 50
        volumes = [1_000_000] * 50
        df = _make_bars(prices, volumes).drop(columns=["volume"])
        with pytest.raises(ValueError, match="missing columns"):
            self.classifier.classify(df)

"""Tests for MeanReversionRegimeClassifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.regimes.classifiers.mean_reversion_regime_classifier import (
    MeanReversionRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import MeanReversionRegime


def _make_frame(
    prices: list[float],
    returns: list[float],
    symbol: str = "AAPL",
) -> pd.DataFrame:
    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(prices))]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices, "ret_1d": returns})


def _mean_reverting_data(n: int = 300) -> tuple[list[float], list[float]]:
    """Prices oscillate tightly around a mean."""
    rng = np.random.default_rng(10)
    prices = list(100.0 + rng.normal(0, 1.0, n))
    returns = [0.0] + [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, n)]
    return prices, returns


def _trending_data(n: int = 300) -> tuple[list[float], list[float]]:
    """Prices drift strongly upward with small noise so z-score std varies."""
    rng = np.random.default_rng(20)
    prices = [100.0 + i * 2.0 + rng.normal(0, 0.1) for i in range(n)]
    returns = [0.0] + [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, n)]
    return prices, returns


class TestMeanReversionRegimeClassifier:
    def setup_method(self):
        self.classifier = MeanReversionRegimeClassifier()

    def test_output_columns_present(self):
        prices, returns = _mean_reverting_data()
        df = _make_frame(prices, returns)
        result = self.classifier.classify(df)
        expected = {
            "symbol",
            "timestamp",
            "regime_mean_reversion",
            "regime_mean_reversion_confidence",
            "mean_reversion_zscore_std",
            "mean_reversion_trend_strength",
        }
        assert expected.issubset(set(result.columns))

    def test_trending_data_has_no_dominant_mean_reverting_signal(self):
        """Trending data should not produce mostly mean_reverting labels.

        Note: some bars may be 'undefined' (mixed signals), but mean_reverting
        should not dominate over trending + undefined combined.
        """
        prices, returns = _trending_data(n=300)
        df = _make_frame(prices, returns)
        result = self.classifier.classify(df)
        non_warmup = result.dropna(subset=["regime_mean_reversion"])
        mr_count = (
            non_warmup["regime_mean_reversion"] == MeanReversionRegime.MEAN_REVERTING.value
        ).sum()
        non_mr_count = len(non_warmup) - mr_count
        assert non_mr_count >= mr_count, (
            f"Expected non-mean-reverting labels to be at least as common as mean_reverting, "
            f"got mr={mr_count} vs non_mr={non_mr_count}"
        )

    def test_mean_reverting_data_produces_some_mr_label(self):
        """Stationary oscillating data should produce at least some mean_reverting labels."""
        prices, returns = _mean_reverting_data(n=300)
        df = _make_frame(prices, returns)
        result = self.classifier.classify(df)
        non_warmup = result.dropna(subset=["regime_mean_reversion"])
        mr_count = (
            non_warmup["regime_mean_reversion"] == MeanReversionRegime.MEAN_REVERTING.value
        ).sum()
        assert mr_count > 0, "Expected at least some mean_reverting labels on oscillating data"

    def test_confidence_between_0_and_1(self):
        prices, returns = _trending_data()
        df = _make_frame(prices, returns)
        result = self.classifier.classify(df)
        valid_conf = result["regime_mean_reversion_confidence"].dropna()
        assert (valid_conf >= 0).all()
        assert (valid_conf <= 1).all()

    def test_deterministic(self):
        prices, returns = _mean_reverting_data()
        df = _make_frame(prices, returns)
        r1 = self.classifier.classify(df)
        r2 = self.classifier.classify(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_missing_required_column_raises(self):
        prices, returns = _mean_reverting_data(50)
        df = _make_frame(prices, returns).drop(columns=["ret_1d"])
        with pytest.raises(ValueError, match="missing columns"):
            self.classifier.classify(df)

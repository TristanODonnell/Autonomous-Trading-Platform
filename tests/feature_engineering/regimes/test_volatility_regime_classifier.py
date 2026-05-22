"""Tests for VolatilityRegimeClassifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.regimes.classifiers.volatility_regime_classifier import (
    VolatilityRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import VolatilityRegime


def _make_returns_frame(
    returns: list[float],
    symbol: str = "AAPL",
) -> pd.DataFrame:
    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(returns))]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "ret_1d": returns})


def _calm_returns(n: int = 200) -> list[float]:
    rng = np.random.default_rng(42)
    return list(rng.normal(0, 0.002, n))


def _volatile_returns(n: int = 200) -> list[float]:
    rng = np.random.default_rng(99)
    return list(rng.normal(0, 0.05, n))


class TestVolatilityRegimeClassifier:
    def setup_method(self):
        self.classifier = VolatilityRegimeClassifier()

    def test_output_columns_present(self):
        df = _make_returns_frame(_calm_returns())
        result = self.classifier.classify(df)
        expected = {
            "symbol",
            "timestamp",
            "regime_volatility",
            "regime_volatility_confidence",
            "volatility_realized",
            "volatility_percentile",
            "volatility_threshold_high",
            "volatility_threshold_low",
        }
        assert expected.issubset(set(result.columns))

    def test_warmup_bars_null(self):
        df = _make_returns_frame(_calm_returns(200))
        result = self.classifier.classify(df, vol_window=20)
        assert result["regime_volatility"].head(19).isna().all()

    def test_high_volatility_regime(self):
        """After enough history, a sustained spike should register high_volatility."""
        calm = _calm_returns(150)
        volatile = _volatile_returns(50)
        df = _make_returns_frame(calm + volatile)
        result = self.classifier.classify(df, vol_window=20)
        last_rows = result.tail(30).dropna(subset=["regime_volatility"])
        high_count = (
            last_rows["regime_volatility"] == VolatilityRegime.HIGH_VOLATILITY.value
        ).sum()
        assert high_count > 0, "Expected high_volatility in the spike period"

    def test_low_volatility_regime(self):
        """Very tight returns at the start should eventually yield low_volatility."""
        rng = np.random.default_rng(7)
        tiny = list(rng.normal(0, 0.0001, 200))
        df = _make_returns_frame(tiny)
        result = self.classifier.classify(df, vol_window=20)
        non_warmup = result.dropna(subset=["regime_volatility"])
        low_count = (non_warmup["regime_volatility"] == VolatilityRegime.LOW_VOLATILITY.value).sum()
        assert low_count > 0

    def test_confidence_between_0_and_1(self):
        df = _make_returns_frame(_calm_returns() + _volatile_returns())
        result = self.classifier.classify(df)
        valid_conf = result["regime_volatility_confidence"].dropna()
        assert (valid_conf >= 0).all()
        assert (valid_conf <= 1).all()

    def test_deterministic(self):
        df = _make_returns_frame(_calm_returns() + _volatile_returns())
        r1 = self.classifier.classify(df)
        r2 = self.classifier.classify(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_missing_required_column_raises(self):
        df = _make_returns_frame(_calm_returns())
        df = df.drop(columns=["ret_1d"])
        with pytest.raises(ValueError, match="missing columns"):
            self.classifier.classify(df)

    def test_thresholds_are_stored(self):
        df = _make_returns_frame(_calm_returns() + _volatile_returns())
        result = self.classifier.classify(df)
        valid = result.dropna(subset=["volatility_threshold_high"])
        assert len(valid) > 0
        assert (valid["volatility_threshold_high"] >= valid["volatility_threshold_low"]).all()

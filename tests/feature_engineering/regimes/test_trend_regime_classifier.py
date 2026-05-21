"""Tests for TrendRegimeClassifier."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.feature_engineering.regimes.classifiers.trend_regime_classifier import (
    TrendRegimeClassifier,
)
from autonomous_trading_platform.feature_engineering.regimes.regime_type import TrendRegime


def _make_trending_up_bars(n: int = 300, symbol: str = "AAPL") -> pd.DataFrame:
    """Monotonically increasing prices — should yield bull regime after warmup."""
    ts = [datetime(2023, 1, 1, tzinfo=UTC)] * n
    ts = [datetime(2023, 1, 1, tzinfo=UTC)]
    from datetime import timedelta

    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    prices = [100.0 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices})


def _make_trending_down_bars(n: int = 300, symbol: str = "AAPL") -> pd.DataFrame:
    from datetime import timedelta

    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    prices = [250.0 - i * 0.5 for i in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices})


def _make_flat_bars(n: int = 300, symbol: str = "AAPL") -> pd.DataFrame:
    from datetime import timedelta

    ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(n)]
    prices = [100.0 + np.sin(i * 0.1) * 1.0 for i in range(n)]
    return pd.DataFrame({"symbol": symbol, "timestamp": ts, "close": prices})


class TestTrendRegimeClassifier:
    def setup_method(self):
        self.classifier = TrendRegimeClassifier()

    def test_output_columns_present(self):
        df = _make_trending_up_bars()
        result = self.classifier.classify(df)
        expected = {
            "symbol",
            "timestamp",
            "regime_trend",
            "regime_trend_confidence",
            "trend_ma_short",
            "trend_ma_long",
            "trend_rolling_return_20d",
        }
        assert expected.issubset(set(result.columns))

    def test_warmup_bars_are_null(self):
        df = _make_trending_up_bars(n=300)
        result = self.classifier.classify(df, short_window=50, long_window=200)
        warmup_rows = result.head(199)
        assert warmup_rows["regime_trend"].isna().all()

    def test_bull_regime_on_uptrend(self):
        df = _make_trending_up_bars(n=300)
        result = self.classifier.classify(df, short_window=50, long_window=200)
        non_warmup = result.dropna(subset=["regime_trend"])
        # With strictly increasing prices short_ma > long_ma
        bull_frac = (non_warmup["regime_trend"] == TrendRegime.BULL.value).mean()
        assert bull_frac > 0.8, f"Expected mostly bull, got {bull_frac:.2%}"

    def test_bear_regime_on_downtrend(self):
        df = _make_trending_down_bars(n=300)
        result = self.classifier.classify(df, short_window=50, long_window=200)
        non_warmup = result.dropna(subset=["regime_trend"])
        bear_frac = (non_warmup["regime_trend"] == TrendRegime.BEAR.value).mean()
        assert bear_frac > 0.8, f"Expected mostly bear, got {bear_frac:.2%}"

    def test_sideways_on_flat_prices(self):
        df = _make_flat_bars(n=300)
        result = self.classifier.classify(df, short_window=50, long_window=200)
        non_warmup = result.dropna(subset=["regime_trend"])
        # Oscillating prices → MA lines close; return can disagree → sideways dominant
        sideways_count = (non_warmup["regime_trend"] == TrendRegime.SIDEWAYS.value).sum()
        assert sideways_count > 0

    def test_confidence_between_0_and_1(self):
        df = _make_trending_up_bars(n=300)
        result = self.classifier.classify(df)
        valid_conf = result["regime_trend_confidence"].dropna()
        assert (valid_conf >= 0).all()
        assert (valid_conf <= 1).all()

    def test_deterministic(self):
        df = _make_trending_up_bars(n=300)
        r1 = self.classifier.classify(df)
        r2 = self.classifier.classify(df)
        pd.testing.assert_frame_equal(r1, r2)

    def test_missing_required_column_raises(self):
        df = _make_trending_up_bars()
        df = df.drop(columns=["close"])
        with pytest.raises(ValueError, match="missing columns"):
            self.classifier.classify(df)

    def test_multi_symbol(self):
        from datetime import timedelta

        ts = [datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(300)]
        aapl = pd.DataFrame(
            {"symbol": "AAPL", "timestamp": ts, "close": [100 + i * 0.5 for i in range(300)]}
        )
        msft = pd.DataFrame(
            {"symbol": "MSFT", "timestamp": ts, "close": [200 - i * 0.5 for i in range(300)]}
        )
        df = pd.concat([aapl, msft], ignore_index=True)
        result = self.classifier.classify(df)
        aapl_r = result[result["symbol"] == "AAPL"].dropna(subset=["regime_trend"])
        msft_r = result[result["symbol"] == "MSFT"].dropna(subset=["regime_trend"])
        assert (aapl_r["regime_trend"] == TrendRegime.BULL.value).mean() > 0.8
        assert (msft_r["regime_trend"] == TrendRegime.BEAR.value).mean() > 0.8

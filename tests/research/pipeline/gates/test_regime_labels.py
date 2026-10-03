"""Unit tests for on-the-fly regime labelling — synthetic bars, no simulation engine."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from autonomous_trading_platform.research.pipeline.gates.regime_labels import (
    REGIME_COLUMNS,
    RegimeClassifierWindows,
    attach_daily_regimes,
    classify_daily_regimes,
    resample_bars_to_daily,
)


def _trading_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _intraday_bars(symbol: str, closes: list[float], bars_per_day: int = 3) -> pd.DataFrame:
    """bars_per_day intraday bars per day; the last bar of each day closes at closes[i]."""
    rows = []
    for day, close in zip(_trading_days(date(2024, 1, 2), len(closes)), closes, strict=True):
        for j in range(bars_per_day):
            ts = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC) + timedelta(
                minutes=5 * j
            )
            rows.append(
                {
                    "symbol": symbol,
                    "timestamp": ts,
                    "close": close - (bars_per_day - 1 - j) * 0.01,
                    "volume": 1_000.0,
                }
            )
    return pd.DataFrame(rows)


def _up_then_down(n_up: int = 40, n_down: int = 40) -> list[float]:
    up = [100.0 * (1.01**i) for i in range(n_up)]
    down = [up[-1] * (0.99**i) for i in range(1, n_down + 1)]
    return up + down


class TestResampleBarsToDaily:
    def test_one_row_per_symbol_day_with_last_close_and_summed_volume(self) -> None:
        bars = _intraday_bars("AAA", [10.0, 11.0], bars_per_day=3)
        daily = resample_bars_to_daily(bars)
        assert len(daily) == 2
        assert list(daily["close"]) == [10.0, 11.0]
        assert list(daily["volume"]) == [3_000.0, 3_000.0]
        # Timestamp of the daily row is the day's last intraday bar.
        assert daily["timestamp"].iloc[0] == bars["timestamp"].iloc[2]

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing columns"):
            resample_bars_to_daily(pd.DataFrame({"symbol": ["A"], "timestamp": [0]}))

    def test_empty_input_returns_empty_frame(self) -> None:
        empty = pd.DataFrame(columns=["symbol", "timestamp", "close", "volume"])
        assert resample_bars_to_daily(empty).empty


class TestClassifyDailyRegimes:
    def test_uptrend_then_downtrend_produces_bull_then_bear(self) -> None:
        bars = _intraday_bars("AAA", _up_then_down())
        labels = classify_daily_regimes(bars)

        assert list(labels.columns) == ["date", *REGIME_COLUMNS]
        trend = labels.set_index("date")["regime_trend"]
        # Warmup days (before the long MA is available) are unlabelled.
        assert trend.iloc[:10].isna().all()
        assert "bull" in set(trend.iloc[20:40].dropna())
        assert trend.iloc[-5:].eq("bear").all()

    def test_is_deterministic(self) -> None:
        bars = _intraday_bars("AAA", _up_then_down())
        pd.testing.assert_frame_equal(classify_daily_regimes(bars), classify_daily_regimes(bars))

    def test_portfolio_label_is_modal_across_symbols(self) -> None:
        up = [100.0 * (1.01**i) for i in range(60)]
        down = [100.0 * (0.99**i) for i in range(60)]
        bars = pd.concat(
            [
                _intraday_bars("UP1", up),
                _intraday_bars("UP2", up),
                _intraday_bars("DOWN", down),
            ],
            ignore_index=True,
        )
        labels = classify_daily_regimes(bars)
        # Two bull symbols vs one bear: the portfolio label is bull once labelled.
        assert labels["regime_trend"].iloc[-10:].eq("bull").all()

    def test_ties_break_alphabetically(self) -> None:
        up = [100.0 * (1.01**i) for i in range(60)]
        down = [100.0 * (0.99**i) for i in range(60)]
        bars = pd.concat(
            [_intraday_bars("UP", up), _intraday_bars("DOWN", down)], ignore_index=True
        )
        labels = classify_daily_regimes(bars)
        # One bull, one bear → tie → "bear" < "bull" alphabetically.
        assert labels["regime_trend"].iloc[-5:].eq("bear").all()

    def test_volatility_dimension_is_labelled(self) -> None:
        rng = np.random.default_rng(7)
        calm = list(100 + np.cumsum(rng.normal(0, 0.1, 40)))
        wild = list(calm[-1] + np.cumsum(rng.normal(0, 3.0, 40)))
        labels = classify_daily_regimes(_intraday_bars("AAA", calm + wild))
        vol = labels["regime_volatility"].dropna()
        assert not vol.empty
        assert vol.iloc[-1] == "high_volatility"

    def test_window_validation(self) -> None:
        with pytest.raises(ValueError, match="trend_short_window"):
            RegimeClassifierWindows(trend_short_window=20, trend_long_window=10)
        with pytest.raises(ValueError, match="percentiles"):
            RegimeClassifierWindows(low_percentile=90.0, high_percentile=80.0)


class TestAttachDailyRegimes:
    def test_each_equity_bar_gets_its_dates_label(self) -> None:
        ts = [
            datetime(2024, 1, 2, 14, 30, tzinfo=UTC),
            datetime(2024, 1, 2, 20, 55, tzinfo=UTC),
            datetime(2024, 1, 3, 14, 30, tzinfo=UTC),
            datetime(2024, 1, 4, 14, 30, tzinfo=UTC),
        ]
        equity = pd.DataFrame({"timestamp": ts, "equity": [100.0, 101.0, 102.0, 103.0]})
        daily = pd.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                **{c: [None, None] for c in REGIME_COLUMNS},
            }
        )
        daily["regime_trend"] = ["bull", "bear"]

        attached = attach_daily_regimes(equity, daily)

        assert list(attached["timestamp"]) == ts
        assert list(attached["regime_trend"][:3]) == ["bull", "bull", "bear"]
        assert pd.isna(attached["regime_trend"].iloc[3])  # no label for Jan 4

    def test_empty_labels_give_all_none(self) -> None:
        equity = pd.DataFrame({"timestamp": [datetime(2024, 1, 2, tzinfo=UTC)], "equity": [100.0]})
        attached = attach_daily_regimes(equity, pd.DataFrame(columns=["date", *REGIME_COLUMNS]))
        assert attached["regime_trend"].isna().all()

from __future__ import annotations

import math

import pytest

from autonomous_trading_platform.strategy.indicators.trend import (
    exponential_moving_average,
    simple_moving_average,
)


class TestSimpleMovingAverage:
    def test_exact_window_length(self) -> None:
        result = simple_moving_average([1.0, 2.0, 3.0], window=3)
        assert result == 2.0

    def test_more_values_than_window_uses_trailing(self) -> None:
        result = simple_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
        assert result == 4.0  # (3+4+5)/3

    def test_window_of_one_returns_last_value(self) -> None:
        result = simple_moving_average([10.0, 20.0, 30.0], window=1)
        assert result == 30.0

    def test_window_of_two_averages_last_two(self) -> None:
        result = simple_moving_average([10.0, 20.0, 30.0], window=2)
        assert result == 25.0

    def test_insufficient_data_returns_none(self) -> None:
        assert simple_moving_average([1.0, 2.0], window=3) is None

    def test_exactly_one_short_returns_none(self) -> None:
        assert simple_moving_average([1.0, 2.0], window=3) is None

    def test_empty_list_returns_none(self) -> None:
        assert simple_moving_average([], window=3) is None

    def test_constant_price_series_returns_that_price(self) -> None:
        result = simple_moving_average([5.0, 5.0, 5.0, 5.0], window=3)
        assert result == 5.0

    def test_negative_window_raises(self) -> None:
        with pytest.raises(ValueError):
            simple_moving_average([1.0, 2.0, 3.0], window=-1)

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            simple_moving_average([1.0, 2.0, 3.0], window=0)

    def test_large_window_golden_value(self) -> None:
        values = [float(i) for i in range(1, 11)]  # [1..10]
        result = simple_moving_average(values, window=5)
        assert result == 8.0  # (6+7+8+9+10)/5


class TestExponentialMovingAverage:
    def test_window_exactly_seeds_ema(self) -> None:
        # Only window bars → seed SMA, no smoothing steps.
        result = exponential_moving_average([1.0, 2.0, 3.0], window=3)
        assert result == 2.0  # seed = (1+2+3)/3

    def test_one_bar_past_window(self) -> None:
        # multiplier = 2/(3+1) = 0.5, seed = (1+2+3)/3 = 2.0
        # after 4: (4-2.0)*0.5 + 2.0 = 3.0
        result = exponential_moving_average([1.0, 2.0, 3.0, 4.0], window=3)
        assert result is not None
        assert math.isclose(result, 3.0)

    def test_two_bars_past_window(self) -> None:
        # multiplier=0.5, seed=2.0 (from [1,2,3])
        # after 4: 3.0; after 5: (5-3.0)*0.5 + 3.0 = 4.0
        result = exponential_moving_average([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
        assert result is not None
        assert math.isclose(result, 4.0)

    def test_insufficient_data_returns_none(self) -> None:
        assert exponential_moving_average([1.0, 2.0], window=3) is None

    def test_empty_list_returns_none(self) -> None:
        assert exponential_moving_average([], window=2) is None

    def test_constant_series_returns_that_constant(self) -> None:
        result = exponential_moving_average([10.0] * 5, window=3)
        assert result is not None
        assert math.isclose(result, 10.0)

    def test_negative_window_raises(self) -> None:
        with pytest.raises(ValueError):
            exponential_moving_average([1.0, 2.0, 3.0], window=-1)

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            exponential_moving_average([1.0, 2.0, 3.0], window=0)

    def test_window_one_reduces_to_last_value(self) -> None:
        # multiplier = 2/(1+1) = 1.0; seed = values[0]; then each step EMA = new value
        result = exponential_moving_average([10.0, 20.0, 30.0], window=1)
        assert result is not None
        assert math.isclose(result, 30.0)

    def test_ema_greater_than_sma_in_accelerating_series(self) -> None:
        # Exponential growth: EMA weights recent bars more heavily, so EMA > SMA
        values = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0]
        ema = exponential_moving_average(values, window=5)
        sma = simple_moving_average(values, window=5)
        assert ema is not None and sma is not None
        assert ema > sma

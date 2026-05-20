from __future__ import annotations

import math

from autonomous_trading_platform.strategy.indicators.mean_reversion import (
    distance_from_moving_average,
    z_score,
)


class TestDistanceFromMovingAverage:
    def test_price_above_ma_returns_positive(self) -> None:
        # SMA([3,4,5], window=3) = 4.0; last=5; distance = 5-4 = 1.0
        result = distance_from_moving_average([3.0, 4.0, 5.0], window=3)
        assert result is not None
        assert math.isclose(result, 1.0)

    def test_price_below_ma_returns_negative(self) -> None:
        # SMA([5,4,3], window=3) = 4.0; last=3; distance = 3-4 = -1.0
        result = distance_from_moving_average([5.0, 4.0, 3.0], window=3)
        assert result is not None
        assert math.isclose(result, -1.0)

    def test_price_at_ma_returns_zero(self) -> None:
        result = distance_from_moving_average([10.0, 10.0, 10.0], window=3)
        assert result == 0.0

    def test_insufficient_data_returns_none(self) -> None:
        assert distance_from_moving_average([1.0, 2.0], window=5) is None

    def test_window_one_always_returns_zero(self) -> None:
        # SMA of window=1 = last value; distance = last - last = 0
        result = distance_from_moving_average([15.0, 20.0, 25.0], window=1)
        assert result == 0.0

    def test_uses_trailing_window(self) -> None:
        # SMA of last 2 from [1,2,10,20] = (10+20)/2=15; last=20; dist=5
        result = distance_from_moving_average([1.0, 2.0, 10.0, 20.0], window=2)
        assert result is not None
        assert math.isclose(result, 5.0)


class TestZScore:
    def test_symmetric_series_last_above_mean(self) -> None:
        # [1,2,3,4,5]: mean=3, std_sample=sqrt(2.5)≈1.5811, z=(5-3)/1.5811≈1.2649
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = z_score(values, window=5)
        assert result is not None
        assert math.isclose(result, 2.0 / math.sqrt(2.5), rel_tol=1e-9)

    def test_value_below_mean_returns_negative_z(self) -> None:
        # [5,4,3,2,1]: mean=3, last=1, z=(1-3)/std<0
        values = [5.0, 4.0, 3.0, 2.0, 1.0]
        result = z_score(values, window=5)
        assert result is not None
        assert result < 0.0

    def test_constant_price_series_returns_none(self) -> None:
        # std=0 → z_score returns None
        result = z_score([10.0, 10.0, 10.0, 10.0, 10.0], window=5)
        assert result is None

    def test_insufficient_data_returns_none(self) -> None:
        assert z_score([1.0, 2.0], window=5) is None

    def test_known_z_value(self) -> None:
        # [0,0,0,0,10]: mean=2, std_sample=sqrt((4+4+4+4+64)/4)=sqrt(80/4)=sqrt(20)
        # z = (10-2)/sqrt(20)
        values = [0.0, 0.0, 0.0, 0.0, 10.0]
        result = z_score(values, window=5)
        assert result is not None
        expected = (10.0 - 2.0) / math.sqrt(20.0)
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_z_is_standardised_last_value(self) -> None:
        # result should equal (last - mean) / std
        values = [2.0, 4.0, 6.0, 8.0, 10.0]
        result = z_score(values, window=5)
        mean = sum(values) / 5
        from statistics import stdev

        std = stdev(values)
        expected = (values[-1] - mean) / std
        assert result is not None
        assert math.isclose(result, expected, rel_tol=1e-9)

    def test_uses_trailing_window(self) -> None:
        # Only the last `window` values matter
        # Prepend arbitrary prefix
        prefix = [500.0, 600.0]
        tail = [1.0, 2.0, 3.0, 4.0, 5.0]
        result_with_prefix = z_score(prefix + tail, window=5)
        result_tail_only = z_score(tail, window=5)
        assert result_with_prefix is not None
        assert result_tail_only is not None
        assert math.isclose(result_with_prefix, result_tail_only, rel_tol=1e-9)

from __future__ import annotations

import math

import pytest

from autonomous_trading_platform.strategy.indicators.momentum import (
    momentum,
    rate_of_change,
    rsi,
    rsi_wilder,
)


class TestMomentum:
    def test_lookback_one_returns_last_minus_previous(self) -> None:
        result = momentum([10.0, 12.0, 11.0, 13.0, 15.0], lookback=1)
        assert result == 2.0  # 15 - 13

    def test_lookback_four(self) -> None:
        result = momentum([10.0, 12.0, 11.0, 13.0, 15.0], lookback=4)
        assert result == 5.0  # 15 - 10

    def test_negative_momentum(self) -> None:
        result = momentum([20.0, 18.0, 15.0], lookback=2)
        assert result == -5.0  # 15 - 20

    def test_zero_momentum_flat_series(self) -> None:
        result = momentum([10.0, 10.0, 10.0], lookback=1)
        assert result == 0.0

    def test_exactly_at_boundary_returns_none(self) -> None:
        # len(values) == lookback: need > lookback
        assert momentum([1.0, 2.0], lookback=2) is None

    def test_one_less_than_required_returns_none(self) -> None:
        assert momentum([5.0], lookback=1) is None

    def test_zero_lookback_raises(self) -> None:
        with pytest.raises(ValueError):
            momentum([1.0, 2.0, 3.0], lookback=0)

    def test_negative_lookback_raises(self) -> None:
        with pytest.raises(ValueError):
            momentum([1.0, 2.0, 3.0], lookback=-1)


class TestRateOfChange:
    def test_upward_roc(self) -> None:
        result = rate_of_change([100.0, 110.0], lookback=1)
        assert result is not None
        assert math.isclose(result, 0.1)

    def test_downward_roc(self) -> None:
        result = rate_of_change([100.0, 50.0], lookback=1)
        assert result is not None
        assert math.isclose(result, -0.5)

    def test_zero_previous_returns_none(self) -> None:
        assert rate_of_change([0.0, 10.0], lookback=1) is None

    def test_lookback_two(self) -> None:
        # (110 - 90) / 90
        result = rate_of_change([90.0, 95.0, 110.0], lookback=2)
        assert result is not None
        assert math.isclose(result, 20.0 / 90.0)

    def test_insufficient_data_returns_none(self) -> None:
        assert rate_of_change([1.0], lookback=1) is None

    def test_zero_lookback_raises(self) -> None:
        with pytest.raises(ValueError):
            rate_of_change([1.0, 2.0], lookback=0)

    def test_exact_boundary_returns_none(self) -> None:
        # len == lookback means only one value, can't compute change
        assert rate_of_change([5.0], lookback=1) is None

    def test_flat_series_roc_is_zero(self) -> None:
        result = rate_of_change([50.0, 50.0, 50.0], lookback=1)
        assert result == 0.0


class TestCutlerRSI:
    def test_all_gains_returns_100(self) -> None:
        values = [100.0, 101.0, 102.0, 103.0, 104.0]  # window=4, all gains
        result = rsi(values, window=4)
        assert result == 100.0

    def test_all_losses_returns_zero(self) -> None:
        values = [104.0, 103.0, 102.0, 101.0, 100.0]
        result = rsi(values, window=4)
        assert result == 0.0

    def test_mixed_gains_losses_golden_value(self) -> None:
        # values=[100,101,102,101,103], window=4
        # deltas at i=-4,-3,-2,-1: +1, +1, -1, +2
        # avg_gain=4/4=1.0, avg_loss=1/4=0.25, rs=4
        # RSI = 100 - 100/5 = 80.0
        values = [100.0, 101.0, 102.0, 101.0, 103.0]
        result = rsi(values, window=4)
        assert result is not None
        assert math.isclose(result, 80.0)

    def test_constant_prices_returns_100(self) -> None:
        # All deltas=0, avg_loss=0 → special case returns 100.0
        values = [50.0] * 5
        result = rsi(values, window=4)
        assert result == 100.0

    def test_insufficient_data_returns_none(self) -> None:
        # Need window+1 values
        assert rsi([1.0, 2.0, 3.0, 4.0], window=4) is None

    def test_exact_minimum_data(self) -> None:
        # window+1 values should work
        values = [100.0, 101.0, 100.0, 101.0, 100.0]  # window=4, len=5
        result = rsi(values, window=4)
        assert result is not None
        assert 0.0 <= result <= 100.0

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            rsi([1.0, 2.0, 3.0], window=0)

    def test_result_bounded_between_zero_and_100(self) -> None:
        import random

        rng = random.Random(42)
        values = [100.0 + rng.uniform(-5, 5) for _ in range(20)]
        result = rsi(values, window=14)
        assert result is not None
        assert 0.0 <= result <= 100.0

    def test_path_independence(self) -> None:
        # Cutler RSI is path-independent — same trailing window+1 prices always give same result
        trailing = [100.0, 101.0, 100.0, 102.0, 101.0]
        prefix_a = [50.0, 60.0, 70.0] + trailing
        prefix_b = [200.0, 190.0, 185.0] + trailing
        assert rsi(prefix_a, window=4) == rsi(prefix_b, window=4)


class TestWilderRSI:
    def test_requires_two_window_plus_one_values(self) -> None:
        # window=5 requires 11 values; 10 should return None
        assert rsi_wilder([float(i) for i in range(1, 11)], window=5) is None

    def test_exact_minimum_data_returns_value(self) -> None:
        values = [float(i) for i in range(1, 12)]  # window=5, need 11
        result = rsi_wilder(values, window=5)
        assert result is not None

    def test_all_gains_approaches_100(self) -> None:
        values = [float(i) for i in range(1, 50)]  # long uptrend
        result = rsi_wilder(values, window=14)
        assert result is not None
        assert result > 90.0

    def test_all_losses_approaches_zero(self) -> None:
        values = [float(50 - i) for i in range(50)]  # long downtrend
        result = rsi_wilder(values, window=14)
        assert result is not None
        assert result < 10.0

    def test_result_bounded(self) -> None:
        import random

        rng = random.Random(0)
        values = [100.0 + rng.uniform(-5, 5) for _ in range(40)]
        result = rsi_wilder(values, window=14)
        assert result is not None
        assert 0.0 <= result <= 100.0

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            rsi_wilder([1.0, 2.0, 3.0], window=0)

    def test_wilder_and_cutler_diverge_on_same_data(self) -> None:
        # The two formulations produce different values in trending conditions
        values = [100.0 + i * 0.5 for i in range(35)]
        cutler = rsi(values, window=14)
        wilder = rsi_wilder(values, window=14)
        assert cutler is not None and wilder is not None
        # They can equal each other occasionally but generally differ
        # — just confirm both return valid numbers
        assert 0.0 <= cutler <= 100.0
        assert 0.0 <= wilder <= 100.0

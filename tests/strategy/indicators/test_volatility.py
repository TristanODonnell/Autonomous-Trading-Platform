from __future__ import annotations

import math

import pytest

from autonomous_trading_platform.strategy.indicators.volatility import (
    realized_volatility,
    rolling_standard_deviation,
)


class TestRollingStandardDeviation:
    def test_golden_value_window_three(self) -> None:
        # subset=[3,4,5]: mean=4, var=((3-4)^2+(4-4)^2+(5-4)^2)/2=1.0, std=1.0
        result = rolling_standard_deviation([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
        assert result is not None
        assert math.isclose(result, 1.0)

    def test_window_equals_len(self) -> None:
        # subset=[1,2,3,4,5]: mean=3, var=(4+1+0+1+4)/4=2.5, std=sqrt(2.5)
        result = rolling_standard_deviation([1.0, 2.0, 3.0, 4.0, 5.0], window=5)
        assert result is not None
        assert math.isclose(result, math.sqrt(2.5), rel_tol=1e-9)

    def test_window_one_returns_zero(self) -> None:
        result = rolling_standard_deviation([10.0, 20.0, 30.0], window=1)
        assert result == 0.0

    def test_constant_series_returns_zero(self) -> None:
        result = rolling_standard_deviation([5.0, 5.0, 5.0, 5.0], window=4)
        assert result == 0.0

    def test_insufficient_data_returns_none(self) -> None:
        assert rolling_standard_deviation([1.0, 2.0], window=3) is None

    def test_empty_list_returns_none(self) -> None:
        assert rolling_standard_deviation([], window=3) is None

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            rolling_standard_deviation([1.0, 2.0, 3.0], window=0)

    def test_negative_window_raises(self) -> None:
        with pytest.raises(ValueError):
            rolling_standard_deviation([1.0, 2.0, 3.0], window=-1)

    def test_uses_sample_variance(self) -> None:
        # Sample variance divides by (n-1) not n
        values = [2.0, 4.0]
        result = rolling_standard_deviation(values, window=2)
        assert result is not None
        # mean=3, sample_var=((2-3)^2+(4-3)^2)/1=2.0, std=sqrt(2)
        assert math.isclose(result, math.sqrt(2.0), rel_tol=1e-9)

    def test_uses_trailing_window_not_full_history(self) -> None:
        # Prepend some outlier values — only last window matters
        result_long = rolling_standard_deviation([1000.0, -1000.0, 3.0, 4.0, 5.0], window=3)
        result_short = rolling_standard_deviation([3.0, 4.0, 5.0], window=3)
        assert result_long is not None
        assert result_short is not None
        assert math.isclose(result_long, result_short, rel_tol=1e-9)

    def test_window_two_golden_value(self) -> None:
        # subset=[10,20]: mean=15, var=(25+25)/1=50, std=sqrt(50)=5*sqrt(2)
        result = rolling_standard_deviation([5.0, 10.0, 20.0], window=2)
        assert result is not None
        assert math.isclose(result, math.sqrt(50.0), rel_tol=1e-9)


class TestRealizedVolatility:
    def test_delegates_to_rolling_std(self) -> None:
        # realized_volatility is an alias for rolling_standard_deviation
        returns = [0.01, -0.02, 0.015, -0.005, 0.02]
        rv = realized_volatility(returns, window=5)
        std = rolling_standard_deviation(returns, window=5)
        assert rv == std

    def test_insufficient_returns_returns_none(self) -> None:
        assert realized_volatility([0.01, 0.02], window=5) is None

    def test_zero_returns_returns_zero(self) -> None:
        result = realized_volatility([0.0, 0.0, 0.0], window=3)
        assert result == 0.0

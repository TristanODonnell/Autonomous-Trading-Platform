from __future__ import annotations

import pytest

from autonomous_trading_platform.strategy.indicators.volume import (
    average_volume,
    volume_ratio,
    volume_spike,
)


class TestAverageVolume:
    def test_exact_window_length(self) -> None:
        result = average_volume([1000.0, 2000.0, 3000.0], window=3)
        assert result == 2000.0

    def test_trailing_window_golden_value(self) -> None:
        # last 2 of [100, 200, 300] = (200+300)/2
        result = average_volume([100.0, 200.0, 300.0], window=2)
        assert result == 250.0

    def test_window_one_returns_last_value(self) -> None:
        result = average_volume([500.0, 1000.0, 750.0], window=1)
        assert result == 750.0

    def test_insufficient_data_returns_none(self) -> None:
        assert average_volume([1000.0, 2000.0], window=3) is None

    def test_empty_list_returns_none(self) -> None:
        assert average_volume([], window=3) is None

    def test_zero_window_raises(self) -> None:
        with pytest.raises(ValueError):
            average_volume([1000.0, 2000.0], window=0)

    def test_negative_window_raises(self) -> None:
        with pytest.raises(ValueError):
            average_volume([1000.0, 2000.0], window=-1)

    def test_constant_volume_returns_that_volume(self) -> None:
        result = average_volume([500.0, 500.0, 500.0, 500.0], window=3)
        assert result == 500.0


class TestVolumeRatio:
    def test_current_equals_average_returns_one(self) -> None:
        # [100, 100, 100, 100] window=4: avg=100, ratio=100/100=1.0
        result = volume_ratio([100.0, 100.0, 100.0, 100.0], window=4)
        assert result == 1.0

    def test_current_twice_average(self) -> None:
        # [100, 100, 100, 300] window=4: avg=(100+100+100+300)/4=150, ratio=300/150=2.0
        result = volume_ratio([100.0, 100.0, 100.0, 300.0], window=4)
        assert result == 2.0

    def test_current_below_average(self) -> None:
        # [200, 200, 200, 100] window=4: avg=700/4=175, ratio=100/175≈0.571
        result = volume_ratio([200.0, 200.0, 200.0, 100.0], window=4)
        assert result is not None
        assert result < 1.0

    def test_zero_average_returns_none(self) -> None:
        # avg == 0 case: can't divide
        result = volume_ratio([0.0, 0.0, 0.0], window=3)
        assert result is None

    def test_insufficient_data_returns_none(self) -> None:
        assert volume_ratio([1000.0], window=3) is None

    def test_window_two(self) -> None:
        # [50, 100, 150] window=2: last 2=[100,150], avg=125, ratio=150/125=1.2
        result = volume_ratio([50.0, 100.0, 150.0], window=2)
        assert result is not None
        assert abs(result - 1.2) < 1e-9


class TestVolumeSpike:
    def test_ratio_equals_threshold_is_spike(self) -> None:
        # [100, 100, 100, 300] window=4: ratio=2.0, threshold=2.0 → spike
        assert volume_spike([100.0, 100.0, 100.0, 300.0], window=4, threshold=2.0) is True

    def test_ratio_above_threshold_is_spike(self) -> None:
        # [50, 50, 50, 200] window=4: avg=(50+50+50+200)/4=87.5, ratio=200/87.5≈2.286>2.0
        assert volume_spike([50.0, 50.0, 50.0, 200.0], window=4, threshold=2.0) is True

    def test_ratio_below_threshold_not_spike(self) -> None:
        # [100, 100, 100, 100] window=4: ratio=1.0 < 2.0 → no spike
        assert volume_spike([100.0, 100.0, 100.0, 100.0], window=4, threshold=2.0) is False

    def test_insufficient_data_returns_false(self) -> None:
        assert volume_spike([1000.0], window=3) is False

    def test_custom_threshold(self) -> None:
        # [100, 100, 100, 130] window=4: avg=(100+100+100+130)/4=107.5, ratio=130/107.5≈1.209
        assert volume_spike([100.0, 100.0, 100.0, 130.0], window=4, threshold=1.5) is False
        # Now threshold=1.2: 1.209 < 1.2 → False still (borderline)
        # Use higher: [100, 100, 100, 155] window=4: avg=455/4=113.75, ratio=155/113.75≈1.363>1.3
        assert volume_spike([100.0, 100.0, 100.0, 155.0], window=4, threshold=1.3) is True

    def test_default_threshold_is_two(self) -> None:
        # With default threshold=2.0
        result_spike = volume_spike([100.0, 100.0, 100.0, 300.0], window=4)
        result_no_spike = volume_spike([100.0, 100.0, 100.0, 100.0], window=4)
        assert result_spike is True
        assert result_no_spike is False

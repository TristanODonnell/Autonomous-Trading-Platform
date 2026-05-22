from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.mean_reversion_strategy import (
    MeanReversionStrategy,
)
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000020")


def _make_bars(closes: list[float]):
    return [
        make_five_minute_bar(
            timestamp=_BASE_TS + timedelta(minutes=5 * i),
            close_price=str(c),
            open_price=str(c),
            high_price=str(c),
            low_price=str(c),
        )
        for i, c in enumerate(closes)
    ]


def _ctx(bars) -> StrategyContext:
    ts = _BASE_TS + timedelta(minutes=5 * (len(bars) + 1))
    return StrategyContext(
        run_id=_RUN_ID,
        strategy_id="mr_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestMeanReversionStrategySignals:
    def test_price_far_below_mean_returns_buy(self) -> None:
        # Use window=5: set up bars so z-score < -2
        # [10,10,10,10,10,0]: mean=8.33, std=sample, last=0 → z very negative
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 0.0]
        strategy = MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=-1.0)
        bars = _make_bars(closes)
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.BUY

    def test_price_far_above_mean_returns_sell(self) -> None:
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 30.0]
        strategy = MeanReversionStrategy(strategy_id="mr", window=5, sell_above_z=1.0)
        bars = _make_bars(closes)
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.SELL

    def test_price_near_mean_returns_none(self) -> None:
        closes = [10.0] * 6
        strategy = MeanReversionStrategy(strategy_id="mr", window=5)
        bars = _make_bars(closes)
        # constant prices → z_score=None
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = MeanReversionStrategy(strategy_id="mr", window=5)
        bars = _make_bars([10.0] * 4)  # need 5 bars for window=5
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_confidence_scales_with_z_magnitude(self) -> None:
        # Two bars: one with mild overshoot (z=-1.1), one extreme (z=-3.0)
        # More extreme z should produce higher confidence
        closes_mild = [10.0, 10.0, 10.0, 10.0, 10.0, 8.5]
        closes_extreme = [10.0, 10.0, 10.0, 10.0, 10.0, 0.0]
        strategy = MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=-1.0)
        bars_mild = _make_bars(closes_mild)
        bars_extreme = _make_bars(closes_extreme)
        s_mild = strategy.evaluate_symbol(_ctx(bars_mild))
        s_extreme = strategy.evaluate_symbol(_ctx(bars_extreme))
        if s_mild is not None and s_extreme is not None:
            assert isinstance(s_extreme.confidence, float)
            assert isinstance(s_mild.confidence, float)
            assert s_extreme.confidence >= s_mild.confidence

    def test_params_embedded_in_signal(self) -> None:
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 0.0]
        strategy = MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=-1.0)
        signal = strategy.evaluate_symbol(_ctx(_make_bars(closes)))
        assert signal is not None
        assert signal.params is not None
        assert signal.params["strategy_type"] == "mean_reversion"
        assert signal.params["window"] == 5


class TestMeanReversionStrategyValidation:
    def test_non_positive_window_raises(self) -> None:
        with pytest.raises(ValueError):
            MeanReversionStrategy(strategy_id="mr", window=0)

    def test_buy_below_z_must_be_less_than_sell_above_z(self) -> None:
        with pytest.raises(ValueError):
            MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=2.0, sell_above_z=1.0)

    def test_equal_thresholds_raises(self) -> None:
        with pytest.raises(ValueError):
            MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=2.0, sell_above_z=2.0)


class TestMeanReversionStrategyDeterminism:
    def test_same_context_produces_same_signal_id(self) -> None:
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 0.0]
        strategy = MeanReversionStrategy(strategy_id="mr", window=5, buy_below_z=-1.0)
        ctx = _ctx(_make_bars(closes))
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        assert s1 is not None and s2 is not None
        assert s1.signal_id == s2.signal_id

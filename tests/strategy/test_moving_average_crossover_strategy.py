from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.moving_average_crossover_strategy import (
    MovingAverageCrossoverStrategy,
)
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000001")


def _make_bars(closes: list[float], base_ts: datetime = _BASE_TS):
    return [
        make_five_minute_bar(
            timestamp=base_ts + timedelta(minutes=5 * i),
            close_price=str(close),
            open_price=str(close),
            high_price=str(close),
            low_price=str(close),
        )
        for i, close in enumerate(closes)
    ]


def _make_context(bars, ts_offset: int = 0) -> StrategyContext:
    ts = _BASE_TS + timedelta(minutes=5 * (len(bars) + ts_offset))
    return StrategyContext(
        run_id=_RUN_ID,
        strategy_id="mac_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestMAcrossoverSignalGeneration:
    def test_bullish_crossover_returns_buy(self) -> None:
        # short_window=3, long_window=5: need 6+ bars
        # Design: fast goes from below slow to above slow on the last bar
        # Build a price series where slow MA crosses: lower prices then spike
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
        # With short=3, long=5:
        # closes[:-1]=[10,10,10,10,10,10]: fast=10, slow=10 (tied)
        # closes=[10,10,10,10,10,10,20]: fast=(10+10+20)/3=13.33, slow=(10+10+10+10+20)/5=12
        # previous_fast(10) <= previous_slow(10) AND current_fast(13.33) > current_slow(12) → BUY
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars(closes)
        context = _make_context(bars)
        signal = strategy.evaluate_symbol(context)
        assert signal is not None
        assert signal.direction == SignalDirection.BUY
        assert signal.symbol == "AAPL"
        assert signal.strategy_id == "mac_test"

    def test_bearish_crossover_returns_sell(self) -> None:
        # Fast crosses below slow
        closes = [20.0, 20.0, 20.0, 20.0, 20.0, 20.0, 5.0]
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars(closes)
        context = _make_context(bars)
        signal = strategy.evaluate_symbol(context)
        assert signal is not None
        assert signal.direction == SignalDirection.SELL

    def test_no_crossover_returns_none(self) -> None:
        # Flat prices: no crossover
        closes = [10.0] * 10
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars(closes)
        context = _make_context(bars)
        signal = strategy.evaluate_symbol(context)
        assert signal is None

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars([10.0] * 5)  # needs 6 (long_window+1)
        context = _make_context(bars)
        signal = strategy.evaluate_symbol(context)
        assert signal is None

    def test_exactly_one_short_of_required_bars_returns_none(self) -> None:
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars([10.0] * 5)  # 5 < 6 required
        context = _make_context(bars)
        assert strategy.evaluate_symbol(context) is None


class TestMAcrossoverSignalDeterminism:
    def test_same_bars_same_signal_id(self) -> None:
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars(closes)
        ctx = _make_context(bars)
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        assert s1 is not None and s2 is not None
        assert s1.signal_id == s2.signal_id

    def test_different_run_id_different_signal_id(self) -> None:
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
        strategy = MovingAverageCrossoverStrategy(
            strategy_id="mac_v1", short_window=3, long_window=5
        )
        bars = _make_bars(closes)

        ts = _BASE_TS + timedelta(minutes=5 * (len(bars) + 1))
        ctx_a = StrategyContext(
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            strategy_id="mac_test",
            symbol="AAPL",
            evaluation_timestamp=ts,
            bar_timestamp=ts - timedelta(minutes=5),
            bars=bars,
        )
        ctx_b = StrategyContext(
            run_id=UUID("00000000-0000-0000-0000-000000000002"),
            strategy_id="mac_test",
            symbol="AAPL",
            evaluation_timestamp=ts,
            bar_timestamp=ts - timedelta(minutes=5),
            bars=bars,
        )
        s_a = strategy.evaluate_symbol(ctx_a)
        s_b = strategy.evaluate_symbol(ctx_b)
        assert s_a is not None and s_b is not None
        assert s_a.signal_id != s_b.signal_id


class TestMAcrossoverValidation:
    def test_short_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageCrossoverStrategy(strategy_id="x", short_window=0, long_window=5)

    def test_long_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageCrossoverStrategy(strategy_id="x", short_window=3, long_window=0)

    def test_short_window_must_be_less_than_long_window(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageCrossoverStrategy(strategy_id="x", short_window=5, long_window=5)

    def test_short_window_greater_than_long_raises(self) -> None:
        with pytest.raises(ValueError):
            MovingAverageCrossoverStrategy(strategy_id="x", short_window=10, long_window=5)

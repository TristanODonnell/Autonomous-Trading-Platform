from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.momentum_strategy import MomentumStrategy
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000010")


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
        strategy_id="momentum_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestMomentumStrategySignals:
    def test_positive_momentum_with_buy_above_zero_returns_buy(self) -> None:
        # momentum(lookback=1) = 105-100 = 5 > 0 → BUY
        strategy = MomentumStrategy(strategy_id="m", lookback=1, buy_above=0.0)
        bars = _make_bars([100.0, 105.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.BUY

    def test_negative_momentum_with_sell_below_zero_returns_sell(self) -> None:
        # momentum = 95-100 = -5 < 0 → SELL
        strategy = MomentumStrategy(strategy_id="m", lookback=1, sell_below=0.0)
        bars = _make_bars([100.0, 95.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.SELL

    def test_flat_momentum_returns_none(self) -> None:
        strategy = MomentumStrategy(strategy_id="m", lookback=1, buy_above=0.0, sell_below=0.0)
        bars = _make_bars([100.0, 100.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_momentum_below_buy_threshold_returns_none(self) -> None:
        # momentum=2, but buy_above=5 → no buy
        strategy = MomentumStrategy(strategy_id="m", lookback=1, buy_above=5.0)
        bars = _make_bars([100.0, 102.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = MomentumStrategy(strategy_id="m", lookback=5, buy_above=0.0)
        bars = _make_bars([100.0, 102.0, 104.0, 106.0, 108.0])  # len=5, need >5
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_lookback_larger_than_one(self) -> None:
        # lookback=3: momentum = 106 - 100 = 6 > 0 → BUY
        strategy = MomentumStrategy(strategy_id="m", lookback=3, buy_above=0.0)
        bars = _make_bars([100.0, 101.0, 103.0, 106.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.BUY


class TestMomentumStrategyDeterminism:
    def test_same_context_same_signal_id(self) -> None:
        strategy = MomentumStrategy(strategy_id="m", lookback=1, buy_above=0.0)
        bars = _make_bars([100.0, 105.0])
        ctx = _ctx(bars)
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        assert s1 is not None and s2 is not None
        assert s1.signal_id == s2.signal_id

    def test_params_embedded_in_signal(self) -> None:
        strategy = MomentumStrategy(strategy_id="m", lookback=2, buy_above=0.0)
        bars = _make_bars([100.0, 105.0, 110.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.params is not None
        assert signal.params["strategy_type"] == "momentum"
        assert signal.params["lookback"] == 2

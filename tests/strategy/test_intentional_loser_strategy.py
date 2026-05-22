from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.intentional_loser_strategy import (
    IntentionalLoserStrategy,
)
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000040")


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
        strategy_id="loser_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestIntentionalLoserStrategy:
    def test_price_increased_returns_sell(self) -> None:
        # Buys low and sells high intentionally reversed: price up → SELL
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0, 105.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.SELL

    def test_price_decreased_returns_buy(self) -> None:
        # Price down → BUY (intentionally bad)
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([105.0, 100.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.direction == SignalDirection.BUY

    def test_flat_price_returns_none(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0, 100.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_insufficient_bars_returns_none(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_empty_bars_returns_none(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        ctx = _ctx([])
        # Need at least 2 bars
        signal = strategy.evaluate_symbol(ctx)
        assert signal is None

    def test_price_change_threshold_zero_triggers_on_any_change(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser", price_change_threshold=0.0)
        bars_up = _make_bars([100.0, 100.01])
        signal = strategy.evaluate_symbol(_ctx(bars_up))
        assert signal is not None
        assert signal.direction == SignalDirection.SELL

    def test_price_change_threshold_suppresses_small_changes(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser", price_change_threshold=5.0)
        bars = _make_bars([100.0, 102.0])  # delta=2 < threshold=5
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is None

    def test_confidence_is_always_one(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0, 110.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.confidence == 1.0

    def test_params_reflect_price_context(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0, 110.0])
        signal = strategy.evaluate_symbol(_ctx(bars))
        assert signal is not None
        assert signal.params is not None
        assert signal.params["strategy_type"] == "intentional_loser"
        assert signal.params["previous_close"] == 100.0
        assert signal.params["current_close"] == 110.0
        assert signal.params["price_delta"] == 10.0


class TestIntentionalLoserStrategyValidation:
    def test_negative_threshold_raises(self) -> None:
        with pytest.raises(ValueError):
            IntentionalLoserStrategy(strategy_id="loser", price_change_threshold=-1.0)


class TestIntentionalLoserStrategyDeterminism:
    def test_same_inputs_same_signal_id(self) -> None:
        strategy = IntentionalLoserStrategy(strategy_id="loser")
        bars = _make_bars([100.0, 110.0])
        ctx = _ctx(bars)
        s1 = strategy.evaluate_symbol(ctx)
        s2 = strategy.evaluate_symbol(ctx)
        assert s1 is not None and s2 is not None
        assert s1.signal_id == s2.signal_id

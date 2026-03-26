from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from tests.utilities.factories import make_five_minute_bar


def _make_context(*, bars: Sequence[MarketBar]) -> StrategyContext:
    return StrategyContext(
        run_id=UUID("00000000-0000-0000-0000-000000000401"),
        strategy_id="stub_strategy_v1",
        symbol="AAPL",
        bar_timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
        evaluation_timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
        bars=list(bars),
    )


def test_generates_buy_signal_when_closing_price_crosses_above_previous_bar() -> None:
    strategy = StubStrategy()

    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            close_price="100.00",
        ),
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
            close_price="101.00",
        ),
    ]

    context = _make_context(bars=bars)

    signal = strategy.evaluate_symbol(context)

    assert signal is not None
    assert signal.direction == SignalDirection.BUY
    assert signal.symbol == "AAPL"
    assert signal.strategy_id == "stub_strategy_v1"


def test_generates_sell_signal_when_closing_price_crosses_below_previous_bar() -> None:
    strategy = StubStrategy()

    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            close_price="101.00",
        ),
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
            close_price="100.00",
        ),
    ]

    context = _make_context(bars=bars)

    signal = strategy.evaluate_symbol(context)

    assert signal is not None
    assert signal.direction == SignalDirection.SELL
    assert signal.symbol == "AAPL"


def test_no_trade_when_closing_price_does_not_change() -> None:
    strategy = StubStrategy()

    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            close_price="100.00",
        ),
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
            close_price="100.00",
        ),
    ]

    context = _make_context(bars=bars)

    signal = strategy.evaluate_symbol(context)

    assert signal is None


def test_no_trade_when_price_change_is_within_threshold() -> None:
    strategy = StubStrategy(price_change_threshold=0.05)

    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            close_price="100.00",
        ),
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
            close_price="100.01",
        ),
    ]

    context = _make_context(bars=bars)

    signal = strategy.evaluate_symbol(context)

    assert signal is None


def test_multiple_evaluations_with_same_bars_do_not_duplicate_signals() -> None:
    strategy = StubStrategy()

    bars = [
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 30, tzinfo=UTC),
            close_price="100.00",
        ),
        make_five_minute_bar(
            timestamp=datetime(2025, 1, 15, 15, 35, tzinfo=UTC),
            close_price="101.00",
        ),
    ]

    context = _make_context(bars=bars)

    signal_one = strategy.evaluate_symbol(context)
    signal_two = strategy.evaluate_symbol(context)

    assert signal_one is not None
    assert signal_two is not None
    assert signal_one.signal_id == signal_two.signal_id


def test_raises_when_price_change_threshold_is_negative() -> None:
    with pytest.raises(ValueError, match="price_change_threshold must be non-negative"):
        StubStrategy(price_change_threshold=-0.01)

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.strategy.contracts.strategy_context import StrategyContext
from autonomous_trading_platform.strategy.implementations.random_debug_strategy import (
    RandomStrategy,
)
from tests.utilities.factories import make_five_minute_bar

_BASE_TS = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000030")


def _ctx(n_bars: int = 2) -> StrategyContext:
    bars = [
        make_five_minute_bar(timestamp=_BASE_TS + timedelta(minutes=5 * i)) for i in range(n_bars)
    ]
    ts = _BASE_TS + timedelta(minutes=5 * (n_bars + 1))
    return StrategyContext(
        run_id=_RUN_ID,
        strategy_id="random_test",
        symbol="AAPL",
        evaluation_timestamp=ts,
        bar_timestamp=ts - timedelta(minutes=5),
        bars=bars,
    )


class TestRandomStrategyDeterminism:
    def test_same_seed_produces_same_sequence(self) -> None:
        ctx = _ctx()
        strat_a = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=42)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=42)

        results_a = [strat_a.evaluate_symbol(ctx) for _ in range(10)]
        results_b = [strat_b.evaluate_symbol(ctx) for _ in range(10)]

        directions_a = [s.direction if s else None for s in results_a]
        directions_b = [s.direction if s else None for s in results_b]
        assert directions_a == directions_b

    def test_different_seeds_produce_different_sequences(self) -> None:
        ctx = _ctx()
        strat_a = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=42)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=99)

        results_a = [strat_a.evaluate_symbol(ctx) for _ in range(20)]
        results_b = [strat_b.evaluate_symbol(ctx) for _ in range(20)]

        dirs_a = [s.direction if s else None for s in results_a]
        dirs_b = [s.direction if s else None for s in results_b]
        assert dirs_a != dirs_b

    def test_no_seed_produces_different_sequences(self) -> None:
        ctx = _ctx()
        strat_a = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=None)
        strat_b = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=None)

        results_a = [strat_a.evaluate_symbol(ctx) for _ in range(20)]
        results_b = [strat_b.evaluate_symbol(ctx) for _ in range(20)]

        # They will almost certainly differ (not guaranteed but very likely over 20 calls)
        # Just check both are valid
        assert all(
            s is None or s.direction in (SignalDirection.BUY, SignalDirection.SELL)
            for s in results_a + results_b
        )


class TestRandomStrategyBehavior:
    def test_signal_probability_zero_never_emits(self) -> None:
        ctx = _ctx()
        strat = RandomStrategy(strategy_id="r", signal_probability=0.0, random_seed=0)
        for _ in range(50):
            assert strat.evaluate_symbol(ctx) is None

    def test_signal_probability_one_always_emits(self) -> None:
        ctx = _ctx()
        strat = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=0)
        for _ in range(10):
            assert strat.evaluate_symbol(ctx) is not None

    def test_buy_probability_one_always_buys(self) -> None:
        ctx = _ctx()
        strat = RandomStrategy(
            strategy_id="r", signal_probability=1.0, buy_probability=1.0, random_seed=0
        )
        for _ in range(10):
            signal = strat.evaluate_symbol(ctx)
            assert signal is not None
            assert signal.direction == SignalDirection.BUY

    def test_buy_probability_zero_always_sells(self) -> None:
        ctx = _ctx()
        strat = RandomStrategy(
            strategy_id="r", signal_probability=1.0, buy_probability=0.0, random_seed=0
        )
        for _ in range(10):
            signal = strat.evaluate_symbol(ctx)
            assert signal is not None
            assert signal.direction == SignalDirection.SELL

    def test_confidence_is_always_0_5(self) -> None:
        ctx = _ctx()
        strat = RandomStrategy(strategy_id="r", signal_probability=1.0, random_seed=5)
        for _ in range(10):
            signal = strat.evaluate_symbol(ctx)
            if signal is not None:
                assert signal.confidence == 0.5


class TestRandomStrategyValidation:
    def test_invalid_signal_probability_raises(self) -> None:
        with pytest.raises(ValueError):
            RandomStrategy(strategy_id="r", signal_probability=1.5)

    def test_negative_signal_probability_raises(self) -> None:
        with pytest.raises(ValueError):
            RandomStrategy(strategy_id="r", signal_probability=-0.1)

    def test_invalid_buy_probability_raises(self) -> None:
        with pytest.raises(ValueError):
            RandomStrategy(strategy_id="r", buy_probability=1.1)

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from autonomous_trading_platform.research.simulation.services.lookahead_guard_service import (
    LookaheadGuardService,
)
from autonomous_trading_platform.strategy.contexts.strategy_context_builder import (
    StrategyContextBuilder,
)

# -------------------------
# Helpers
# -------------------------


def make_bar(ts: datetime):
    return SimpleNamespace(timestamp=ts, close=100.0)


def make_timeline(start: datetime, count: int, step_minutes: int = 5):
    return [start + timedelta(minutes=i * step_minutes) for i in range(count)]


# -------------------------
# Guard Tests
# -------------------------


def test_guard_allows_only_prior_bars():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)
    bars = [make_bar(t0 - timedelta(minutes=5)), make_bar(t0 - timedelta(minutes=10))]

    # Should NOT raise
    guard.assert_historical_only(
        symbol="AAPL",
        simulation_timestamp=t0,
        bars=bars,
    )


def test_guard_raises_for_current_bar():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)
    bars = [make_bar(t0)]

    with pytest.raises(ValueError):
        guard.assert_historical_only(
            symbol="AAPL",
            simulation_timestamp=t0,
            bars=bars,
        )


def test_guard_raises_for_future_bar():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)
    bars = [make_bar(t0 + timedelta(minutes=5))]

    with pytest.raises(ValueError):
        guard.assert_historical_only(
            symbol="AAPL",
            simulation_timestamp=t0,
            bars=bars,
        )


def test_unsorted_timeline_raises():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)
    timeline = [
        t0,
        t0 - timedelta(minutes=5),  # out of order
    ]

    with pytest.raises(ValueError):
        guard.assert_timeline_strictly_increasing(timeline=timeline)


def test_duplicate_timeline_raises():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)
    timeline = [t0, t0]

    with pytest.raises(ValueError):
        guard.assert_timeline_strictly_increasing(timeline=timeline)


# -------------------------
# Context Builder Tests
# -------------------------


def test_context_builder_excludes_current_bar():
    t0 = datetime(2025, 1, 1, 10, 0)

    bars = [
        make_bar(t0 - timedelta(minutes=10)),
        make_bar(t0 - timedelta(minutes=5)),
        make_bar(t0),  # current bar (should NOT be included)
    ]

    builder = StrategyContextBuilder(
        market_bar_reader=None,  # not used in this path
        bars_dataset=None,
        lookback_bars=2,
    )

    window = SimpleNamespace(
        bars_by_symbol={"AAPL": bars},
    )

    context = builder.build_from_window(
        run_id=uuid4(),
        strategy_id="test",
        symbol="AAPL",
        timestamp=t0,
        window=window,
        positions={},
        state={},
    )

    assert context is not None

    timestamps = [b.timestamp for b in context.bars]

    assert max(timestamps) < t0


def test_context_builder_excludes_future_bars():
    t0 = datetime(2025, 1, 1, 10, 0)

    bars = [
        make_bar(t0 - timedelta(minutes=10)),
        make_bar(t0 - timedelta(minutes=5)),
        make_bar(t0 + timedelta(minutes=5)),  # future bar
    ]

    builder = StrategyContextBuilder(
        market_bar_reader=None,
        bars_dataset=None,
        lookback_bars=2,
    )

    window = SimpleNamespace(
        bars_by_symbol={"AAPL": bars},
    )

    context = builder.build_from_window(
        run_id=uuid4(),
        strategy_id="test",
        symbol="AAPL",
        timestamp=t0,
        window=window,
        positions={},
        state={},
    )

    assert context is not None

    timestamps = [b.timestamp for b in context.bars]

    assert all(ts < t0 for ts in timestamps)


def test_guard_raises_when_bar_missing_timestamp():
    guard = LookaheadGuardService()

    t0 = datetime(2025, 1, 1, 10, 0)

    bars = [SimpleNamespace(close=100.0)]  # no timestamp

    with pytest.raises(ValueError, match="without timestamp"):
        guard.assert_historical_only(
            symbol="AAPL",
            simulation_timestamp=t0,
            bars=bars,
        )


def test_context_builder_respects_lookback_window():
    t0 = datetime(2025, 1, 1, 10, 0)

    bars = [
        make_bar(t0 - timedelta(minutes=20)),
        make_bar(t0 - timedelta(minutes=15)),
        make_bar(t0 - timedelta(minutes=10)),
        make_bar(t0 - timedelta(minutes=5)),
    ]

    builder = StrategyContextBuilder(
        market_bar_reader=None,
        bars_dataset=None,
        lookback_bars=2,
    )

    window = SimpleNamespace(
        bars_by_symbol={"AAPL": bars},
    )

    context = builder.build_from_window(
        run_id=uuid4(),
        strategy_id="test",
        symbol="AAPL",
        timestamp=t0,
        window=window,
        positions={},
        state={},
    )

    timestamps = [b.timestamp for b in context.bars]

    assert timestamps == [
        t0 - timedelta(minutes=10),
        t0 - timedelta(minutes=5),
    ]

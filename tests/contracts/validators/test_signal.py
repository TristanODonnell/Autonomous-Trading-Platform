from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.contracts.validators.core import run_rules
from autonomous_trading_platform.contracts.validators.signal import SIGNAL_RULES


def make_signal(**overrides) -> Signal:
    ts = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)

    data = {
        "signal_id": uuid4(),
        "run_id": uuid4(),
        "timestamp": ts,
        "bar_timestamp": ts,
        "strategy_id": "strategy_001",
        "symbol": "AAPL",
        "direction": SignalDirection.BUY,
        "confidence": 0.8,
        "target_position": Decimal("10"),
        "params": None,
    }

    data.update(overrides)
    return Signal(**data)


def test_valid_signal_passes():
    signal = make_signal()

    result = run_rules(signal, SIGNAL_RULES)

    assert result.ok
    assert result.violations == []


def test_bar_timestamp_must_align_to_5min_boundary():
    signal = make_signal(bar_timestamp=datetime(2025, 1, 1, 10, 1, tzinfo=UTC))

    result = run_rules(signal, SIGNAL_RULES)

    assert not result.ok
    assert any(v.code == "BAR_TIMESTAMP_ALIGNED_5MIN_BOUNDARY" for v in result.violations)


def test_flat_direction_requires_zero_target_position():
    signal = make_signal(
        direction=SignalDirection.FLAT,
        target_position=Decimal("5"),
    )

    result = run_rules(signal, SIGNAL_RULES)

    assert not result.ok
    assert any(v.code == "FLAT_DIRECTION_REQUIRES_ZERO_TARGET_POSITION" for v in result.violations)


def test_confidence_must_be_between_zero_and_one():
    signal = make_signal(confidence=1.5)

    result = run_rules(signal, SIGNAL_RULES)

    assert not result.ok
    assert any(v.code == "CONFIDENCE_IN_RANGE_WHEN_PRESENT" for v in result.violations)


def test_valid_flat_signal_passes():
    signal = make_signal(direction=SignalDirection.FLAT, target_position=0)

    result = run_rules(signal, SIGNAL_RULES)

    assert result.ok


def test_confidence_none_is_valid():
    signal = make_signal(confidence=None)

    result = run_rules(signal, SIGNAL_RULES)

    assert result.ok

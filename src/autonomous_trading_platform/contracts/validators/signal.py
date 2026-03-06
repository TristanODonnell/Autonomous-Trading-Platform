# autonomous_trading_platform/contracts/validators/signal.py

from __future__ import annotations

from autonomous_trading_platform.contracts.common.enums import SignalDirection
from autonomous_trading_platform.contracts.trading.signal import Signal

from .core import Rule, is_aligned_to_minutes

SIGNAL_RULES: list[Rule[Signal]] = [
    Rule(
        code="BAR_TIMESTAMP_ALIGNED_5MIN_BOUNDARY",
        field="bar_timestamp",
        check=lambda s, _ctx: is_aligned_to_minutes(s.bar_timestamp, 5),
        message=lambda s, _ctx: "bar_timestamp must align to 5-minute boundary",
    ),
    Rule(
        code="FLAT_DIRECTION_REQUIRES_ZERO_TARGET_POSITION",
        field="target_position",
        check=lambda s, _ctx: s.direction != SignalDirection.FLAT or s.target_position in (None, 0),
        message=lambda s, _ctx: "when direction='flat', target_position must be 0 if present",
    ),
    Rule(
        code="CONFIDENCE_IN_RANGE_WHEN_PRESENT",
        field="confidence",
        check=lambda s, _ctx: s.confidence is None or (0 <= s.confidence <= 1),
        message=lambda s, _ctx: "confidence must be between 0 and 1 when present",
    ),
]

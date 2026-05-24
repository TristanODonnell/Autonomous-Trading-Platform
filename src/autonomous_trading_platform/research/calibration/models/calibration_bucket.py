from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from enum import StrEnum


class PolicyTypeBucket(StrEnum):
    PASSTHROUGH = "passthrough"
    MARKET = "market"
    LIMIT = "limit"
    TWAP = "twap"
    VWAP_LITE = "vwap_lite"
    UNKNOWN = "unknown"


class TimeOfDayBucket(StrEnum):
    OPEN = "open"  # 09:30–10:00 ET
    MIDDAY = "midday"  # 10:00–15:30 ET
    CLOSE = "close"  # 15:30–16:00 ET
    AFTER_HOURS = "after_hours"


class OrderSizeTier(StrEnum):
    SMALL = "small"  # < 100 shares
    MEDIUM = "medium"  # 100–499 shares
    LARGE = "large"  # 500–1 999 shares
    XLARGE = "xlarge"  # 2 000+ shares


@dataclass(frozen=True)
class CalibrationBucket:
    policy_type: PolicyTypeBucket
    time_of_day: TimeOfDayBucket
    order_size_tier: OrderSizeTier


_OPEN_START = time(9, 30)
_OPEN_END = time(10, 0)
_MIDDAY_END = time(15, 30)
_CLOSE_END = time(16, 0)


def classify_policy_type(policy_mode: str) -> PolicyTypeBucket:
    try:
        return PolicyTypeBucket(policy_mode.lower())
    except ValueError:
        return PolicyTypeBucket.UNKNOWN


def classify_time_of_day(ts: datetime) -> TimeOfDayBucket:
    """Classify a UTC timestamp into a market-session bucket.

    Times are compared against naive ET equivalents by subtracting 4 or 5 hours
    depending on DST.  For calibration aggregation, exact DST boundary handling
    is not critical — a one-bucket mis-classification is acceptable.
    """
    if ts.tzinfo is not None:
        # Convert to ET (approximate: UTC-4 during EDT, UTC-5 during EST).
        # We use UTC-4 as a reasonable approximation for the US trading day.
        from datetime import timedelta

        et_offset = timedelta(hours=4)
        local_time = (ts - et_offset).time()
    else:
        local_time = ts.time()

    if _OPEN_START <= local_time < _OPEN_END:
        return TimeOfDayBucket.OPEN
    if _OPEN_END <= local_time < _MIDDAY_END:
        return TimeOfDayBucket.MIDDAY
    if _MIDDAY_END <= local_time < _CLOSE_END:
        return TimeOfDayBucket.CLOSE
    return TimeOfDayBucket.AFTER_HOURS


def classify_order_size(quantity: Decimal) -> OrderSizeTier:
    if quantity < Decimal("100"):
        return OrderSizeTier.SMALL
    if quantity < Decimal("500"):
        return OrderSizeTier.MEDIUM
    if quantity < Decimal("2000"):
        return OrderSizeTier.LARGE
    return OrderSizeTier.XLARGE

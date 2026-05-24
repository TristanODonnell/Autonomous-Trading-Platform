"""Tests for calibration bucket classification utilities."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from autonomous_trading_platform.research.calibration.models.calibration_bucket import (
    OrderSizeTier,
    PolicyTypeBucket,
    TimeOfDayBucket,
    classify_order_size,
    classify_policy_type,
    classify_time_of_day,
)


class TestClassifyPolicyType:
    def test_known_modes(self):
        assert classify_policy_type("passthrough") == PolicyTypeBucket.PASSTHROUGH
        assert classify_policy_type("market") == PolicyTypeBucket.MARKET
        assert classify_policy_type("limit") == PolicyTypeBucket.LIMIT
        assert classify_policy_type("twap") == PolicyTypeBucket.TWAP
        assert classify_policy_type("vwap_lite") == PolicyTypeBucket.VWAP_LITE

    def test_case_insensitive(self):
        assert classify_policy_type("PASSTHROUGH") == PolicyTypeBucket.PASSTHROUGH
        assert classify_policy_type("TWAP") == PolicyTypeBucket.TWAP

    def test_unknown_falls_back(self):
        assert classify_policy_type("algo_xyz") == PolicyTypeBucket.UNKNOWN
        assert classify_policy_type("") == PolicyTypeBucket.UNKNOWN


class TestClassifyTimeOfDay:
    def _utc(self, hour: int, minute: int = 0) -> datetime:
        # UTC-4 offset applied inside classify_time_of_day for ET approximation.
        # So UTC 13:30 → ET 09:30 → OPEN.
        return datetime(2024, 6, 15, hour, minute, tzinfo=UTC)

    def test_open_session(self):
        # ET 09:30 = UTC 13:30
        assert classify_time_of_day(self._utc(13, 30)) == TimeOfDayBucket.OPEN
        assert classify_time_of_day(self._utc(13, 45)) == TimeOfDayBucket.OPEN

    def test_midday_session(self):
        # ET 10:00 = UTC 14:00
        assert classify_time_of_day(self._utc(14, 0)) == TimeOfDayBucket.MIDDAY
        assert classify_time_of_day(self._utc(17, 0)) == TimeOfDayBucket.MIDDAY

    def test_close_session(self):
        # ET 15:30 = UTC 19:30
        assert classify_time_of_day(self._utc(19, 30)) == TimeOfDayBucket.CLOSE
        assert classify_time_of_day(self._utc(19, 45)) == TimeOfDayBucket.CLOSE

    def test_after_hours(self):
        # Before 09:30 ET
        assert classify_time_of_day(self._utc(12, 0)) == TimeOfDayBucket.AFTER_HOURS
        # After 16:00 ET
        assert classify_time_of_day(self._utc(21, 0)) == TimeOfDayBucket.AFTER_HOURS

    def test_naive_datetime_uses_local_time(self):
        ts = datetime(2024, 6, 15, 9, 30)  # naive
        result = classify_time_of_day(ts)
        assert result in TimeOfDayBucket  # just assert it doesn't raise


class TestClassifyOrderSize:
    def test_small(self):
        assert classify_order_size(Decimal("1")) == OrderSizeTier.SMALL
        assert classify_order_size(Decimal("99")) == OrderSizeTier.SMALL

    def test_medium(self):
        assert classify_order_size(Decimal("100")) == OrderSizeTier.MEDIUM
        assert classify_order_size(Decimal("499")) == OrderSizeTier.MEDIUM

    def test_large(self):
        assert classify_order_size(Decimal("500")) == OrderSizeTier.LARGE
        assert classify_order_size(Decimal("1999")) == OrderSizeTier.LARGE

    def test_xlarge(self):
        assert classify_order_size(Decimal("2000")) == OrderSizeTier.XLARGE
        assert classify_order_size(Decimal("100000")) == OrderSizeTier.XLARGE

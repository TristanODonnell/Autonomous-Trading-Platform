"""Tests for FillQualityAggregator — grouping, stats, and edge cases."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from autonomous_trading_platform.research.calibration.models.calibration_bucket import (
    OrderSizeTier,
    PolicyTypeBucket,
)
from autonomous_trading_platform.research.calibration.services.fill_quality_aggregator import (
    FillQualityAggregator,
)


def _row(
    *,
    slippage_bps: str | None,
    policy_mode: str = "passthrough",
    quantity: str | None = "100",
    signal_timestamp: datetime | None = None,
    is_adverse_fill: bool = False,
    record_id: str = "r1",
) -> MagicMock:
    row = MagicMock()
    row.record_id = record_id
    row.slippage_bps = Decimal(slippage_bps) if slippage_bps is not None else None
    row.policy_mode = policy_mode
    row.quantity = Decimal(quantity) if quantity is not None else None
    row.is_adverse_fill = is_adverse_fill
    # UTC 14:00 → ET 10:00 → MIDDAY
    row.signal_timestamp = signal_timestamp or datetime(2024, 6, 15, 14, 0, tzinfo=UTC)
    return row


class TestFillQualityAggregatorBasic:
    def test_empty_input_returns_empty(self):
        agg = FillQualityAggregator()
        assert agg.aggregate([]) == []

    def test_skips_null_slippage(self):
        agg = FillQualityAggregator()
        rows = [_row(slippage_bps=None, record_id="r1"), _row(slippage_bps="5", record_id="r2")]
        stats = agg.aggregate(rows)
        assert len(stats) == 1
        assert stats[0].sample_count == 1

    def test_skips_null_quantity(self):
        agg = FillQualityAggregator()
        rows = [_row(slippage_bps="5", quantity=None, record_id="r1")]
        stats = agg.aggregate(rows)
        assert len(stats) == 0

    def test_single_record_creates_one_bucket(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [_row(slippage_bps="5")]
        stats = agg.aggregate(rows)
        assert len(stats) == 1
        s = stats[0]
        assert s.sample_count == 1
        assert s.mean_slippage_bps == Decimal("5")
        assert s.is_sufficient_sample is True

    def test_grouping_by_policy_type(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [
            _row(slippage_bps="5", policy_mode="passthrough", record_id="r1"),
            _row(slippage_bps="10", policy_mode="twap", record_id="r2"),
        ]
        stats = agg.aggregate(rows)
        assert len(stats) == 2
        policy_types = {s.bucket.policy_type for s in stats}
        assert PolicyTypeBucket.PASSTHROUGH in policy_types
        assert PolicyTypeBucket.TWAP in policy_types

    def test_grouping_by_order_size_tier(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [
            _row(slippage_bps="5", quantity="50", record_id="r1"),  # SMALL
            _row(slippage_bps="8", quantity="200", record_id="r2"),  # MEDIUM
        ]
        stats = agg.aggregate(rows)
        assert len(stats) == 2
        tiers = {s.bucket.order_size_tier for s in stats}
        assert OrderSizeTier.SMALL in tiers
        assert OrderSizeTier.MEDIUM in tiers

    def test_mean_median_p90_computation(self):
        agg = FillQualityAggregator(min_samples=1)
        # 10 records with slippage 1, 2, ..., 10
        rows = [_row(slippage_bps=str(i), record_id=f"r{i}") for i in range(1, 11)]
        stats = agg.aggregate(rows)
        assert len(stats) == 1
        s = stats[0]
        assert s.sample_count == 10
        # mean = 5.5
        assert s.mean_slippage_bps == Decimal("5.5000")
        # median = index 5 of sorted [1..10] = 6
        assert s.median_slippage_bps == Decimal("6.0000")
        # p90 = index int(10*0.9)=9 => sorted[9]=10
        assert s.p90_slippage_bps == Decimal("10.0000")

    def test_adverse_fill_rate(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [
            _row(slippage_bps="5", is_adverse_fill=True, record_id="r1"),
            _row(slippage_bps="5", is_adverse_fill=False, record_id="r2"),
            _row(slippage_bps="5", is_adverse_fill=False, record_id="r3"),
            _row(slippage_bps="5", is_adverse_fill=False, record_id="r4"),
        ]
        stats = agg.aggregate(rows)
        assert len(stats) == 1
        assert stats[0].adverse_fill_rate == Decimal("0.2500")

    def test_insufficient_sample_flag(self):
        agg = FillQualityAggregator(min_samples=30)
        rows = [_row(slippage_bps="5", record_id=f"r{i}") for i in range(5)]
        stats = agg.aggregate(rows)
        assert all(not s.is_sufficient_sample for s in stats)

    def test_sufficient_sample_flag_at_threshold(self):
        agg = FillQualityAggregator(min_samples=3)
        rows = [_row(slippage_bps="5", record_id=f"r{i}") for i in range(3)]
        stats = agg.aggregate(rows)
        assert all(s.is_sufficient_sample for s in stats)


class TestAggregatorDeterminism:
    def test_same_input_same_output(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [_row(slippage_bps=str(i), record_id=f"r{i}") for i in range(1, 6)]
        result1 = agg.aggregate(rows)
        result2 = agg.aggregate(rows)
        assert len(result1) == len(result2)
        for s1, s2 in zip(result1, result2, strict=True):
            assert s1 == s2

    def test_output_is_sorted(self):
        agg = FillQualityAggregator(min_samples=1)
        rows = [
            _row(slippage_bps="5", policy_mode="twap", record_id="r1"),
            _row(slippage_bps="5", policy_mode="passthrough", record_id="r2"),
        ]
        stats = agg.aggregate(rows)
        policy_order = [s.bucket.policy_type.value for s in stats]
        assert policy_order == sorted(policy_order)

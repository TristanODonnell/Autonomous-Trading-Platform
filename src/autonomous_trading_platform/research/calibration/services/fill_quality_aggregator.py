"""Aggregates FillQualityMetrics rows into per-bucket calibration statistics."""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.calibration.models.calibration_bucket import (
    CalibrationBucket,
    classify_order_size,
    classify_policy_type,
    classify_time_of_day,
)
from autonomous_trading_platform.research.calibration.models.calibration_snapshot import (
    MIN_CALIBRATION_SAMPLES,
    BucketCalibrationStats,
)
from autonomous_trading_platform.storage.sor.models.fill_quality_metrics import FillQualityMetrics

logger = get_logger(__name__)

_QUANT = Decimal("0.0001")


def _quantize(v: Decimal) -> Decimal:
    return v.quantize(_QUANT, rounding=ROUND_HALF_UP)


class _BucketAccumulator:
    """Accumulates raw slippage values for a single bucket."""

    __slots__ = ("slippage_bps_values", "adverse_count")

    def __init__(self) -> None:
        self.slippage_bps_values: list[Decimal] = []
        self.adverse_count: int = 0

    def add(self, slippage_bps: Decimal, is_adverse: bool) -> None:
        self.slippage_bps_values.append(slippage_bps)
        if is_adverse:
            self.adverse_count += 1

    def to_stats(self, bucket: CalibrationBucket, min_samples: int) -> BucketCalibrationStats:
        n = len(self.slippage_bps_values)
        if n == 0:
            return BucketCalibrationStats(
                bucket=bucket,
                sample_count=0,
                mean_slippage_bps=Decimal("0"),
                median_slippage_bps=Decimal("0"),
                p90_slippage_bps=Decimal("0"),
                adverse_fill_rate=Decimal("0"),
                is_sufficient_sample=False,
            )
        sorted_vals = sorted(self.slippage_bps_values)
        mean_val = _quantize(sum(sorted_vals, Decimal("0")) / n)
        median_val = _quantize(sorted_vals[n // 2])
        p90_idx = min(int(n * 0.9), n - 1)
        p90_val = _quantize(sorted_vals[p90_idx])
        adverse_rate = _quantize(Decimal(self.adverse_count) / Decimal(n))
        return BucketCalibrationStats(
            bucket=bucket,
            sample_count=n,
            mean_slippage_bps=mean_val,
            median_slippage_bps=median_val,
            p90_slippage_bps=p90_val,
            adverse_fill_rate=adverse_rate,
            is_sufficient_sample=n >= min_samples,
        )


class FillQualityAggregator:
    """Groups FillQualityMetrics rows into calibration buckets and computes stats.

    Rows with NULL slippage_bps are silently skipped (fill not yet complete).
    Rows with NULL quantity are skipped with a warning.
    """

    def __init__(self, min_samples: int = MIN_CALIBRATION_SAMPLES) -> None:
        self._min_samples = min_samples

    def aggregate(self, records: list[FillQualityMetrics]) -> list[BucketCalibrationStats]:
        accumulators: dict[CalibrationBucket, _BucketAccumulator] = defaultdict(_BucketAccumulator)
        skipped = 0

        for row in records:
            if row.slippage_bps is None:
                skipped += 1
                continue
            if row.quantity is None or row.quantity <= 0:
                logger.warning(
                    "calibration.aggregator.skipped_row",
                    extra={"reason": "missing_quantity", "record_id": row.record_id},
                )
                skipped += 1
                continue
            if row.signal_timestamp is None:
                logger.warning(
                    "calibration.aggregator.skipped_row",
                    extra={"reason": "missing_timestamp", "record_id": row.record_id},
                )
                skipped += 1
                continue

            bucket = CalibrationBucket(
                policy_type=classify_policy_type(row.policy_mode or "unknown"),
                time_of_day=classify_time_of_day(row.signal_timestamp),
                order_size_tier=classify_order_size(Decimal(str(row.quantity))),
            )
            accumulators[bucket].add(
                slippage_bps=Decimal(str(row.slippage_bps)),
                is_adverse=bool(row.is_adverse_fill),
            )

        if skipped:
            logger.debug(
                "calibration.aggregator.skipped",
                extra={"skipped_count": skipped, "total": len(records)},
            )

        return [
            acc.to_stats(bucket, self._min_samples)
            for bucket, acc in sorted(
                accumulators.items(),
                key=lambda kv: (
                    kv[0].policy_type.value,
                    kv[0].time_of_day.value,
                    kv[0].order_size_tier.value,
                ),
            )
        ]

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import cast

from autonomous_trading_platform.research.calibration.models.calibration_bucket import (
    CalibrationBucket,
    OrderSizeTier,
    PolicyTypeBucket,
    TimeOfDayBucket,
)

# Minimum fills required for a bucket to be considered calibrated.
MIN_CALIBRATION_SAMPLES: int = 30

# Assumed typical volume share used when deriving impact_coefficient_bps from
# observed mean slippage.  Documents the derivation clearly: users can override.
DEFAULT_ASSUMED_VOLUME_SHARE: Decimal = Decimal("0.02")

CALIBRATION_VERSION: str = "1.0.0"


@dataclass(frozen=True)
class BucketCalibrationStats:
    bucket: CalibrationBucket
    sample_count: int
    mean_slippage_bps: Decimal
    median_slippage_bps: Decimal
    p90_slippage_bps: Decimal
    adverse_fill_rate: Decimal
    is_sufficient_sample: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "bucket": {
                "policy_type": self.bucket.policy_type.value,
                "time_of_day": self.bucket.time_of_day.value,
                "order_size_tier": self.bucket.order_size_tier.value,
            },
            "sample_count": self.sample_count,
            "mean_slippage_bps": str(self.mean_slippage_bps),
            "median_slippage_bps": str(self.median_slippage_bps),
            "p90_slippage_bps": str(self.p90_slippage_bps),
            "adverse_fill_rate": str(self.adverse_fill_rate),
            "is_sufficient_sample": self.is_sufficient_sample,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> BucketCalibrationStats:
        b = d["bucket"]  # type: ignore[index]
        bucket = CalibrationBucket(
            policy_type=PolicyTypeBucket(b["policy_type"]),  # type: ignore[index]
            time_of_day=TimeOfDayBucket(b["time_of_day"]),  # type: ignore[index]
            order_size_tier=OrderSizeTier(b["order_size_tier"]),  # type: ignore[index]
        )
        return cls(
            bucket=bucket,
            sample_count=int(str(d["sample_count"])),
            mean_slippage_bps=Decimal(str(d["mean_slippage_bps"])),
            median_slippage_bps=Decimal(str(d["median_slippage_bps"])),
            p90_slippage_bps=Decimal(str(d["p90_slippage_bps"])),
            adverse_fill_rate=Decimal(str(d["adverse_fill_rate"])),
            is_sufficient_sample=bool(d["is_sufficient_sample"]),
        )


@dataclass
class SlippageCalibrationSnapshot:
    """Versioned snapshot of calibrated slippage parameters.

    Derived from realized fill-quality data over a calibration window.
    Consumed by VolumeShareSlippageModel.from_calibration_snapshot() to
    produce a calibrated model instance for simulation.

    Derivation of calibrated_impact_coefficient_bps:
        impact_coefficient_bps = mean_slippage_bps / assumed_typical_volume_share
    where assumed_typical_volume_share represents the expected fraction of bar
    volume consumed by a typical order.  Default: 2% (configurable).
    """

    calibration_id: str
    generated_at: datetime
    calibration_window_start: datetime
    calibration_window_end: datetime
    source_fill_count: int
    calibration_version: str
    bucket_stats: list[BucketCalibrationStats] = field(default_factory=list)

    # Global calibrated parameters derived from all sufficient-sample data.
    calibrated_impact_coefficient_bps: Decimal = Decimal("25")
    calibrated_fallback_min_bps: Decimal = Decimal("5")
    calibrated_max_volume_share: Decimal = Decimal("0.10")
    assumed_typical_volume_share: Decimal = DEFAULT_ASSUMED_VOLUME_SHARE

    # True when global calibration has sufficient data (>= MIN_CALIBRATION_SAMPLES).
    is_globally_calibrated: bool = False

    def is_valid(self) -> bool:
        return self.is_globally_calibrated

    def to_dict(self) -> dict[str, object]:
        return {
            "calibration_id": self.calibration_id,
            "generated_at": self.generated_at.isoformat(),
            "calibration_window_start": self.calibration_window_start.isoformat(),
            "calibration_window_end": self.calibration_window_end.isoformat(),
            "source_fill_count": self.source_fill_count,
            "calibration_version": self.calibration_version,
            "calibrated_impact_coefficient_bps": str(self.calibrated_impact_coefficient_bps),
            "calibrated_fallback_min_bps": str(self.calibrated_fallback_min_bps),
            "calibrated_max_volume_share": str(self.calibrated_max_volume_share),
            "assumed_typical_volume_share": str(self.assumed_typical_volume_share),
            "is_globally_calibrated": self.is_globally_calibrated,
            "bucket_stats": [s.to_dict() for s in self.bucket_stats],
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SlippageCalibrationSnapshot:
        from datetime import datetime

        def _dt(v: object) -> datetime:
            return datetime.fromisoformat(str(v))

        raw_stats = cast(list[object], d.get("bucket_stats") or [])
        bucket_stats = [
            BucketCalibrationStats.from_dict(b)  # type: ignore[arg-type]
            for b in raw_stats
        ]
        return cls(
            calibration_id=str(d["calibration_id"]),
            generated_at=_dt(d["generated_at"]),
            calibration_window_start=_dt(d["calibration_window_start"]),
            calibration_window_end=_dt(d["calibration_window_end"]),
            source_fill_count=int(str(d["source_fill_count"])),
            calibration_version=str(d["calibration_version"]),
            calibrated_impact_coefficient_bps=Decimal(str(d["calibrated_impact_coefficient_bps"])),
            calibrated_fallback_min_bps=Decimal(str(d["calibrated_fallback_min_bps"])),
            calibrated_max_volume_share=Decimal(str(d["calibrated_max_volume_share"])),
            assumed_typical_volume_share=Decimal(str(d["assumed_typical_volume_share"])),
            is_globally_calibrated=bool(d["is_globally_calibrated"]),
            bucket_stats=bucket_stats,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SlippageCalibrationSnapshot:
        return cls.from_dict(json.loads(raw))

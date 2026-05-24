"""SlippageCalibrationService — closed-loop calibration from realized fill quality data."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.calibration.models.calibration_snapshot import (
    CALIBRATION_VERSION,
    DEFAULT_ASSUMED_VOLUME_SHARE,
    MIN_CALIBRATION_SAMPLES,
    BucketCalibrationStats,
    SlippageCalibrationSnapshot,
)
from autonomous_trading_platform.research.calibration.services.fill_quality_aggregator import (
    FillQualityAggregator,
)
from autonomous_trading_platform.research.simulation.models.volume_share_slippage_model import (
    VolumeShareSlippageConfig,
)
from autonomous_trading_platform.storage.sor.repositories.core.fill_quality_metrics_repository import (
    FillQualityMetricsRepository,
)

logger = get_logger(__name__)

_QUANT = Decimal("0.0001")
_DEFAULT_CONFIG = VolumeShareSlippageConfig()


def _q(v: Decimal) -> Decimal:
    return v.quantize(_QUANT, rounding=ROUND_HALF_UP)


class SlippageCalibrationService:
    """Calibrates simulation slippage assumptions from realized fill-quality data.

    Flow:
        FillQualityMetrics in window
          → aggregate by (policy_type, time_of_day, order_size_tier)
          → compute realized slippage statistics
          → derive global calibrated parameters
          → return versioned SlippageCalibrationSnapshot

    Calibration derivation
    ----------------------
    calibrated_impact_coefficient_bps = global_mean_slippage_bps / assumed_typical_volume_share

    where assumed_typical_volume_share is the expected fraction of bar volume consumed by
    a typical order (default 2%).  This makes the derivation explicit and reproducible.

    Fallback behavior
    -----------------
    If the total fill count is below min_samples, the snapshot is still generated but
    is_globally_calibrated=False and all calibrated parameters retain their safe defaults.
    This prevents unstable coefficients from sparse data.
    """

    def __init__(
        self,
        repository: FillQualityMetricsRepository,
        min_samples: int = MIN_CALIBRATION_SAMPLES,
        assumed_typical_volume_share: Decimal = DEFAULT_ASSUMED_VOLUME_SHARE,
        default_config: VolumeShareSlippageConfig | None = None,
    ) -> None:
        self._repo = repository
        self._min_samples = min_samples
        self._assumed_volume_share = assumed_typical_volume_share
        self._default_config = default_config or _DEFAULT_CONFIG
        self._aggregator = FillQualityAggregator(min_samples=min_samples)

    def calibrate(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        now_utc: datetime | None = None,
    ) -> SlippageCalibrationSnapshot:
        """Generate a calibration snapshot from fills in [window_start, window_end].

        Args:
            window_start: Inclusive start of the calibration window (UTC).
            window_end:   Inclusive end of the calibration window (UTC).
            now_utc:      Override for the generated_at timestamp; defaults to utcnow().

        Returns:
            SlippageCalibrationSnapshot with calibrated parameters and per-bucket stats.
        """
        generated_at = now_utc if now_utc is not None else datetime.now(tz=UTC)
        calibration_id = str(uuid4())

        records = self._repo.get_for_calibration(
            window_start=window_start,
            window_end=window_end,
        )
        source_fill_count = len(records)

        logger.info(
            "calibration.started",
            extra={
                "calibration_id": calibration_id,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "source_fill_count": source_fill_count,
                "min_samples": self._min_samples,
            },
        )

        bucket_stats = self._aggregator.aggregate(records)

        (
            impact_coeff_bps,
            fallback_min_bps,
            max_volume_share,
            is_calibrated,
        ) = self._derive_global_params(bucket_stats, source_fill_count)

        snapshot = SlippageCalibrationSnapshot(
            calibration_id=calibration_id,
            generated_at=generated_at,
            calibration_window_start=window_start,
            calibration_window_end=window_end,
            source_fill_count=source_fill_count,
            calibration_version=CALIBRATION_VERSION,
            bucket_stats=bucket_stats,
            calibrated_impact_coefficient_bps=impact_coeff_bps,
            calibrated_fallback_min_bps=fallback_min_bps,
            calibrated_max_volume_share=max_volume_share,
            assumed_typical_volume_share=self._assumed_volume_share,
            is_globally_calibrated=is_calibrated,
        )

        self._log_calibration_result(snapshot)
        return snapshot

    def _derive_global_params(
        self,
        bucket_stats: list[BucketCalibrationStats],
        source_fill_count: int,
    ) -> tuple[Decimal, Decimal, Decimal, bool]:
        """Derive calibrated VolumeShareSlippageConfig parameters from aggregate stats.

        Returns (impact_coefficient_bps, fallback_min_bps, max_volume_share, is_calibrated).
        Falls back to defaults when sample count is insufficient.
        """
        if source_fill_count < self._min_samples:
            logger.warning(
                "calibration.insufficient_samples",
                extra={
                    "source_fill_count": source_fill_count,
                    "min_samples": self._min_samples,
                    "action": "using_defaults",
                },
            )
            return (
                self._default_config.impact_coefficient_bps,
                self._default_config.fallback_min_bps,
                self._default_config.max_volume_share,
                False,
            )

        # Use only buckets with sufficient data for the global computation.
        sufficient = [s for s in bucket_stats if s.is_sufficient_sample]
        all_sufficient = sufficient if sufficient else bucket_stats

        if not all_sufficient:
            return (
                self._default_config.impact_coefficient_bps,
                self._default_config.fallback_min_bps,
                self._default_config.max_volume_share,
                False,
            )

        total_samples = Decimal(sum(s.sample_count for s in all_sufficient))
        # Weighted mean slippage across sufficient buckets.
        weighted_mean: Decimal = (
            sum((s.mean_slippage_bps * s.sample_count for s in all_sufficient), Decimal("0"))
            / total_samples
        )

        # impact_coefficient_bps derived from observed mean slippage and assumed volume share.
        if self._assumed_volume_share > 0:
            impact_coeff_bps = _q(weighted_mean / self._assumed_volume_share)
        else:
            impact_coeff_bps = self._default_config.impact_coefficient_bps

        # fallback_min_bps: use weighted median approximation as conservative floor.
        weighted_median: Decimal = (
            sum((s.median_slippage_bps * s.sample_count for s in all_sufficient), Decimal("0"))
            / total_samples
        )
        fallback_min_bps = _q(weighted_median)

        # Clamp to reasonable bounds to prevent overfitting to extreme samples.
        impact_coeff_bps = max(Decimal("1"), min(impact_coeff_bps, Decimal("500")))
        fallback_min_bps = max(Decimal("0.5"), min(fallback_min_bps, Decimal("50")))

        return (
            impact_coeff_bps,
            fallback_min_bps,
            self._default_config.max_volume_share,
            True,
        )

    def _log_calibration_result(self, snapshot: SlippageCalibrationSnapshot) -> None:
        sufficient_buckets = sum(1 for s in snapshot.bucket_stats if s.is_sufficient_sample)
        logger.info(
            "calibration.completed",
            extra={
                "calibration_id": snapshot.calibration_id,
                "is_globally_calibrated": snapshot.is_globally_calibrated,
                "source_fill_count": snapshot.source_fill_count,
                "bucket_count": len(snapshot.bucket_stats),
                "sufficient_bucket_count": sufficient_buckets,
                "calibrated_impact_coefficient_bps": str(
                    snapshot.calibrated_impact_coefficient_bps
                ),
                "calibrated_fallback_min_bps": str(snapshot.calibrated_fallback_min_bps),
                "calibration_version": snapshot.calibration_version,
            },
        )

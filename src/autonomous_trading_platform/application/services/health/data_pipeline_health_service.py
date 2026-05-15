from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.detailed_health import (
    HealthCheckName,
    HealthCheckResult,
    HealthSeverity,
    HealthStatus,
    ServiceHealthReport,
)
from autonomous_trading_platform.storage.sor.repositories.queries.runtime_soak_verification_repository import (
    RuntimeSoakVerificationRepository,
)

_RAW_BARS_STALE_THRESHOLD = timedelta(hours=2)
_FEATURE_STALE_THRESHOLD = timedelta(hours=2)
_MANIFEST_STALE_THRESHOLD = timedelta(hours=1)
_INGESTION_LAG_WARN = timedelta(minutes=30)
_INGESTION_LAG_CRITICAL = timedelta(hours=1)
_FEATURE_LAG_WARN = timedelta(minutes=45)
_FEATURE_LAG_CRITICAL = timedelta(hours=2)


def _ok(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.OK,
        severity=HealthSeverity.INFO,
        message=message,
        metadata=metadata,
    )


def _degraded(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.DEGRADED,
        severity=HealthSeverity.WARNING,
        message=message,
        metadata=metadata,
    )


def _critical(name: HealthCheckName, message: str, metadata: dict) -> HealthCheckResult:
    return HealthCheckResult(
        check_name=name,
        status=HealthStatus.CRITICAL,
        severity=HealthSeverity.CRITICAL,
        message=message,
        metadata=metadata,
    )


def _derive_status(checks: list[HealthCheckResult]) -> HealthStatus:
    if any(c.status == HealthStatus.CRITICAL for c in checks):
        return HealthStatus.CRITICAL
    if any(c.status == HealthStatus.DEGRADED for c in checks):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


def _lag_seconds(artifact_time: datetime | None, now: datetime) -> int | None:
    if artifact_time is None:
        return None
    return max(0, int((now - artifact_time).total_seconds()))


class DataPipelineHealthService:
    """
    TASK-612 — validates freshness of raw bars, features, manifests,
    ingestion lag, and feature lag.
    """

    def __init__(
        self,
        session: Session,
        *,
        repository: RuntimeSoakVerificationRepository | None = None,
        raw_bars_stale_threshold: timedelta = _RAW_BARS_STALE_THRESHOLD,
        feature_stale_threshold: timedelta = _FEATURE_STALE_THRESHOLD,
        manifest_stale_threshold: timedelta = _MANIFEST_STALE_THRESHOLD,
        ingestion_lag_warn: timedelta = _INGESTION_LAG_WARN,
        ingestion_lag_critical: timedelta = _INGESTION_LAG_CRITICAL,
        feature_lag_warn: timedelta = _FEATURE_LAG_WARN,
        feature_lag_critical: timedelta = _FEATURE_LAG_CRITICAL,
    ) -> None:
        self._session = session
        self._repo = repository or RuntimeSoakVerificationRepository(session)
        self._raw_bars_stale_threshold = raw_bars_stale_threshold
        self._feature_stale_threshold = feature_stale_threshold
        self._manifest_stale_threshold = manifest_stale_threshold
        self._ingestion_lag_warn = ingestion_lag_warn
        self._ingestion_lag_critical = ingestion_lag_critical
        self._feature_lag_warn = feature_lag_warn
        self._feature_lag_critical = feature_lag_critical

    def check(self) -> ServiceHealthReport:
        now = datetime.now(UTC)
        raw_bars = self._repo.get_latest_raw_bars_dataset()
        features = self._repo.get_latest_feature_dataset()
        manifest = self._repo.get_latest_trading_manifest()

        checks = [
            self._check_raw_bars_freshness(raw_bars, now),
            self._check_feature_freshness(features, now),
            self._check_manifest_freshness(manifest, now),
            self._check_ingestion_lag(raw_bars, now),
            self._check_feature_lag(features, raw_bars, now),
        ]
        return ServiceHealthReport(
            service="data_pipeline",
            status=_derive_status(checks),
            checks=checks,
        )

    def _check_raw_bars_freshness(self, dataset, now: datetime) -> HealthCheckResult:
        threshold_s = int(self._raw_bars_stale_threshold.total_seconds())
        lag = _lag_seconds(getattr(dataset, "created_at", None), now)
        metadata = {
            "threshold_seconds": threshold_s,
            "lag_seconds": lag,
            "dataset_version_id": getattr(dataset, "dataset_version_id", None),
            "created_at": dataset.created_at.isoformat() if dataset is not None else None,
            "validation_status": getattr(dataset, "validation_status", None),
        }

        if dataset is None:
            return _critical(
                HealthCheckName.DATA_RAW_BARS_FRESHNESS,
                "No validated raw bars dataset found.",
                metadata,
            )
        if lag is not None and lag > threshold_s:
            return _degraded(
                HealthCheckName.DATA_RAW_BARS_FRESHNESS,
                f"Raw bars dataset is stale: {lag}s since last validated dataset (threshold {threshold_s}s).",
                metadata,
            )
        return _ok(
            HealthCheckName.DATA_RAW_BARS_FRESHNESS,
            "Raw bars dataset is fresh.",
            metadata,
        )

    def _check_feature_freshness(self, dataset, now: datetime) -> HealthCheckResult:
        threshold_s = int(self._feature_stale_threshold.total_seconds())
        lag = _lag_seconds(getattr(dataset, "created_at", None), now)
        metadata = {
            "threshold_seconds": threshold_s,
            "lag_seconds": lag,
            "dataset_version_id": getattr(dataset, "dataset_version_id", None),
            "created_at": dataset.created_at.isoformat() if dataset is not None else None,
            "validation_status": getattr(dataset, "validation_status", None),
        }

        if dataset is None:
            return _critical(
                HealthCheckName.DATA_FEATURE_FRESHNESS,
                "No validated feature dataset found.",
                metadata,
            )
        if lag is not None and lag > threshold_s:
            return _degraded(
                HealthCheckName.DATA_FEATURE_FRESHNESS,
                f"Feature dataset is stale: {lag}s since last validated dataset (threshold {threshold_s}s).",
                metadata,
            )
        return _ok(
            HealthCheckName.DATA_FEATURE_FRESHNESS,
            "Feature dataset is fresh.",
            metadata,
        )

    def _check_manifest_freshness(self, manifest, now: datetime) -> HealthCheckResult:
        threshold_s = int(self._manifest_stale_threshold.total_seconds())
        lag = _lag_seconds(getattr(manifest, "created_at", None), now)
        metadata = {
            "threshold_seconds": threshold_s,
            "lag_seconds": lag,
            "run_id": str(manifest.run_id) if manifest is not None else None,
            "created_at": manifest.created_at.isoformat() if manifest is not None else None,
            "status": getattr(manifest, "status", None),
            "last_successful_step": getattr(manifest, "last_successful_step", None),
        }

        if manifest is None:
            return _critical(
                HealthCheckName.DATA_MANIFEST_FRESHNESS,
                "No trading manifest found.",
                metadata,
            )
        if lag is not None and lag > threshold_s:
            return _degraded(
                HealthCheckName.DATA_MANIFEST_FRESHNESS,
                f"Trading manifest is stale: {lag}s since last manifest (threshold {threshold_s}s).",
                metadata,
            )
        return _ok(
            HealthCheckName.DATA_MANIFEST_FRESHNESS,
            "Trading manifest is fresh.",
            metadata,
        )

    def _check_ingestion_lag(self, dataset, now: datetime) -> HealthCheckResult:
        lag = _lag_seconds(getattr(dataset, "created_at", None), now)
        warn_s = int(self._ingestion_lag_warn.total_seconds())
        critical_s = int(self._ingestion_lag_critical.total_seconds())
        metadata = {
            "lag_seconds": lag,
            "warn_threshold_seconds": warn_s,
            "critical_threshold_seconds": critical_s,
            "dataset_version_id": getattr(dataset, "dataset_version_id", None),
        }

        if lag is None:
            return _critical(
                HealthCheckName.DATA_INGESTION_LAG,
                "Cannot compute ingestion lag — no validated raw bars dataset found.",
                metadata,
            )
        if lag > critical_s:
            return _critical(
                HealthCheckName.DATA_INGESTION_LAG,
                f"Ingestion lag is critical: {lag}s (threshold {critical_s}s).",
                metadata,
            )
        if lag > warn_s:
            return _degraded(
                HealthCheckName.DATA_INGESTION_LAG,
                f"Ingestion lag elevated: {lag}s (warn threshold {warn_s}s).",
                metadata,
            )
        return _ok(
            HealthCheckName.DATA_INGESTION_LAG,
            f"Ingestion lag is within bounds: {lag}s.",
            metadata,
        )

    def _check_feature_lag(self, features, raw_bars, now: datetime) -> HealthCheckResult:
        features_created = getattr(features, "created_at", None)
        raw_created = getattr(raw_bars, "created_at", None)

        if features_created is None or raw_created is None:
            lag_behind_raw: int | None = None
        else:
            lag_behind_raw = max(0, int((features_created - raw_created).total_seconds()))

        lag_from_now = _lag_seconds(features_created, now)
        warn_s = int(self._feature_lag_warn.total_seconds())
        critical_s = int(self._feature_lag_critical.total_seconds())

        metadata = {
            "feature_dataset_version_id": getattr(features, "dataset_version_id", None),
            "feature_created_at": features_created.isoformat() if features_created else None,
            "raw_bars_created_at": raw_created.isoformat() if raw_created else None,
            "lag_behind_raw_seconds": lag_behind_raw,
            "lag_from_now_seconds": lag_from_now,
            "warn_threshold_seconds": warn_s,
            "critical_threshold_seconds": critical_s,
        }

        if features is None:
            return _critical(
                HealthCheckName.DATA_FEATURE_LAG,
                "Cannot compute feature lag — no validated feature dataset found.",
                metadata,
            )
        if lag_from_now is not None and lag_from_now > critical_s:
            return _critical(
                HealthCheckName.DATA_FEATURE_LAG,
                f"Feature dataset lag is critical: {lag_from_now}s since last features (threshold {critical_s}s).",
                metadata,
            )
        if lag_from_now is not None and lag_from_now > warn_s:
            return _degraded(
                HealthCheckName.DATA_FEATURE_LAG,
                f"Feature dataset lag elevated: {lag_from_now}s (warn threshold {warn_s}s).",
                metadata,
            )
        return _ok(
            HealthCheckName.DATA_FEATURE_LAG,
            f"Feature dataset lag is within bounds: {lag_from_now}s.",
            metadata,
        )

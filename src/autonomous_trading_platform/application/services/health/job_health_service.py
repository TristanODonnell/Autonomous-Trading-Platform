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

_EXPECTED_JOBS = (
    "market_ingestion_cycle",
    "feature_pipeline_cycle",
    "trading_cycle",
)

_STALE_THRESHOLD = timedelta(minutes=15)
_HUNG_THRESHOLD = timedelta(minutes=30)
_ORPHAN_THRESHOLD = timedelta(hours=1)
_DEAD_JOB_WINDOW = timedelta(hours=2)


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


class JobHealthService:
    """
    TASK-611 — detects stale, dead, hung, duplicate-running, and orphaned jobs.

    All checks are point-in-time against the DB — no assumptions about
    which process owns which job record.
    """

    def __init__(
        self,
        session: Session,
        *,
        repository: RuntimeSoakVerificationRepository | None = None,
        stale_threshold: timedelta = _STALE_THRESHOLD,
        hung_threshold: timedelta = _HUNG_THRESHOLD,
        orphan_threshold: timedelta = _ORPHAN_THRESHOLD,
        dead_job_window: timedelta = _DEAD_JOB_WINDOW,
        expected_jobs: tuple[str, ...] = _EXPECTED_JOBS,
    ) -> None:
        self._session = session
        self._repo = repository or RuntimeSoakVerificationRepository(session)
        self._stale_threshold = stale_threshold
        self._hung_threshold = hung_threshold
        self._orphan_threshold = orphan_threshold
        self._dead_job_window = dead_job_window
        self._expected_jobs = expected_jobs

    def check(self) -> ServiceHealthReport:
        now = datetime.now(UTC)
        checks = [
            self._check_stale(now),
            self._check_hung(now),
            self._check_orphaned(now),
            self._check_duplicate_running(),
            self._check_dead(now),
        ]
        return ServiceHealthReport(
            service="job_health",
            status=_derive_status(checks),
            checks=checks,
        )

    def _check_stale(self, now: datetime) -> HealthCheckResult:
        cutoff = now - self._stale_threshold
        stale = self._repo.list_stale_running_runtime_jobs(cutoff=cutoff)
        metadata = {
            "stale_threshold_minutes": int(self._stale_threshold.total_seconds() / 60),
            "cutoff": cutoff.isoformat(),
            "stale_job_count": len(stale),
            "stale_jobs": [
                {
                    "job_run_id": r.job_run_id,
                    "job_name": r.job_name,
                    "started_at": r.started_at.isoformat(),
                    "running_minutes": int((now - r.started_at).total_seconds() / 60),
                }
                for r in stale
            ],
        }

        if stale:
            return _degraded(
                HealthCheckName.JOB_STALE,
                f"{len(stale)} job(s) have been RUNNING longer than {int(self._stale_threshold.total_seconds() / 60)} minutes.",
                metadata,
            )
        return _ok(HealthCheckName.JOB_STALE, "No stale running jobs detected.", metadata)

    def _check_hung(self, now: datetime) -> HealthCheckResult:
        cutoff = now - self._hung_threshold
        hung = self._repo.list_stale_running_runtime_jobs(cutoff=cutoff)
        metadata = {
            "hung_threshold_minutes": int(self._hung_threshold.total_seconds() / 60),
            "cutoff": cutoff.isoformat(),
            "hung_job_count": len(hung),
            "hung_jobs": [
                {
                    "job_run_id": r.job_run_id,
                    "job_name": r.job_name,
                    "started_at": r.started_at.isoformat(),
                    "running_minutes": int((now - r.started_at).total_seconds() / 60),
                }
                for r in hung
            ],
        }

        if hung:
            return _critical(
                HealthCheckName.JOB_HUNG,
                f"{len(hung)} job(s) have been RUNNING for more than {int(self._hung_threshold.total_seconds() / 60)} minutes — likely deadlocked.",
                metadata,
            )
        return _ok(HealthCheckName.JOB_HUNG, "No hung jobs detected.", metadata)

    def _check_orphaned(self, now: datetime) -> HealthCheckResult:
        cutoff = now - self._orphan_threshold
        orphaned = self._repo.list_stale_running_runtime_jobs(cutoff=cutoff)
        metadata = {
            "orphan_threshold_hours": int(self._orphan_threshold.total_seconds() / 3600),
            "cutoff": cutoff.isoformat(),
            "orphaned_job_count": len(orphaned),
            "orphaned_jobs": [
                {
                    "job_run_id": r.job_run_id,
                    "job_name": r.job_name,
                    "started_at": r.started_at.isoformat(),
                }
                for r in orphaned
            ],
        }

        if orphaned:
            return _critical(
                HealthCheckName.JOB_ORPHANED,
                f"{len(orphaned)} job(s) appear orphaned (RUNNING for >{int(self._orphan_threshold.total_seconds() / 3600)}h) — likely left by a crashed process.",
                metadata,
            )
        return _ok(HealthCheckName.JOB_ORPHANED, "No orphaned job records detected.", metadata)

    def _check_duplicate_running(self) -> HealthCheckResult:
        concurrent = self._repo.list_concurrent_running_job_names()
        metadata = {
            "duplicate_running_job_names": sorted(concurrent),
            "duplicate_count": len(concurrent),
        }

        if concurrent:
            return _critical(
                HealthCheckName.JOB_DUPLICATE_RUNNING,
                f"Multiple RUNNING records for the same job name detected: {sorted(concurrent)}. No-overlap lock may have been bypassed.",
                metadata,
            )
        return _ok(
            HealthCheckName.JOB_DUPLICATE_RUNNING,
            "No duplicate RUNNING job records detected.",
            metadata,
        )

    def _check_dead(self, now: datetime) -> HealthCheckResult:
        window_start = now - self._dead_job_window
        window_end = now
        dead: list[str] = []

        for job_name in self._expected_jobs:
            latest = self._repo.get_latest_successful_job_run_by_name(
                job_name=job_name,
                window_start=window_start,
                window_end=window_end,
            )
            if latest is None:
                dead.append(job_name)

        metadata = {
            "window_hours": int(self._dead_job_window.total_seconds() / 3600),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "expected_jobs": list(self._expected_jobs),
            "dead_jobs": dead,
            "dead_count": len(dead),
        }

        if dead:
            return _critical(
                HealthCheckName.JOB_DEAD,
                f"{len(dead)} expected job(s) have no successful completion in the last {int(self._dead_job_window.total_seconds() / 3600)}h: {dead}",
                metadata,
            )
        return _ok(
            HealthCheckName.JOB_DEAD,
            "All expected jobs have a recent successful completion.",
            metadata,
        )

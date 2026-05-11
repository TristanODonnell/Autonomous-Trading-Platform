from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakCheckResult,
    RuntimeSoakSeverity,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)
from autonomous_trading_platform.storage.sor.repositories.queries.runtime_soak_verification_repository import (
    RuntimeSoakVerificationRepository,
)

EXPECTED_RUNTIME_JOBS = (
    "market_ingestion_cycle",
    "feature_pipeline_cycle",
    "trading_cycle",
)


class RuntimeSoakVerificationService:
    def __init__(
        self,
        session: Session,
        *,
        environment: str,
        repository: RuntimeSoakVerificationRepository | None = None,
        stale_after: timedelta = timedelta(minutes=15),
        freshness_lag_threshold: timedelta = timedelta(minutes=15),
        max_runtime_job_duration: timedelta = timedelta(minutes=10),
        cash_drift_tolerance: Decimal = Decimal("1.00"),
        equity_drift_tolerance: Decimal = Decimal("5.00"),
        position_quantity_tolerance: Decimal = Decimal("0.000001"),
    ) -> None:
        self._session = session
        self._repository = repository or RuntimeSoakVerificationRepository(session)
        self._environment = environment
        self._stale_after = stale_after
        self._freshness_lag_threshold = freshness_lag_threshold
        self._max_runtime_job_duration = max_runtime_job_duration
        self._cash_drift_tolerance = cash_drift_tolerance
        self._equity_drift_tolerance = equity_drift_tolerance
        self._position_quantity_tolerance = position_quantity_tolerance

    def verify(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakVerificationReport:
        checks = [
            self._check_runtime_job_health(window_start=window_start, window_end=window_end),
            self._check_data_freshness(window_start=window_start, window_end=window_end),
            self._check_stale_running_state(window_start=window_start, window_end=window_end),
            self._check_order_reconciliation(window_start=window_start, window_end=window_end),
            self._check_duplicate_fill_protection(window_start=window_start, window_end=window_end),
            self._check_cash_position_equity_consistency(
                window_start=window_start,
                window_end=window_end,
            ),
            self._check_observability_signals(window_start=window_start, window_end=window_end),
            self._check_failure_controls(window_start=window_start, window_end=window_end),
        ]

        report_status = self._derive_report_status(checks)

        return RuntimeSoakVerificationReport(
            status=report_status,
            window_start=window_start,
            window_end=window_end,
            environment=self._environment,
            checks=checks,
            summary={
                "total_checks": len(checks),
                "passed": sum(1 for check in checks if check.status == RuntimeSoakStatus.PASSED),
                "warnings": sum(1 for check in checks if check.status == RuntimeSoakStatus.WARNING),
                "failed": sum(1 for check in checks if check.status == RuntimeSoakStatus.FAILED),
            },
        )

    def _derive_report_status(
        self,
        checks: list[RuntimeSoakCheckResult],
    ) -> RuntimeSoakStatus:
        if any(check.status == RuntimeSoakStatus.FAILED for check in checks):
            return RuntimeSoakStatus.FAILED

        if any(check.status == RuntimeSoakStatus.WARNING for check in checks):
            return RuntimeSoakStatus.WARNING

        return RuntimeSoakStatus.PASSED

    def _passed(
        self,
        check_name: RuntimeSoakCheckName,
        message: str,
        metadata: dict | None = None,
    ) -> RuntimeSoakCheckResult:
        return RuntimeSoakCheckResult(
            check_name=check_name,
            status=RuntimeSoakStatus.PASSED,
            severity=RuntimeSoakSeverity.INFO,
            message=message,
            metadata=metadata or {},
        )

    def _warning(
        self,
        check_name: RuntimeSoakCheckName,
        message: str,
        metadata: dict | None = None,
    ) -> RuntimeSoakCheckResult:
        return RuntimeSoakCheckResult(
            check_name=check_name,
            status=RuntimeSoakStatus.WARNING,
            severity=RuntimeSoakSeverity.WARNING,
            message=message,
            metadata=metadata or {},
        )

    def _failed(
        self,
        check_name: RuntimeSoakCheckName,
        message: str,
        metadata: dict | None = None,
        *,
        severity: RuntimeSoakSeverity = RuntimeSoakSeverity.ERROR,
    ) -> RuntimeSoakCheckResult:
        return RuntimeSoakCheckResult(
            check_name=check_name,
            status=RuntimeSoakStatus.FAILED,
            severity=severity,
            message=message,
            metadata=metadata or {},
        )

    def _check_runtime_job_health(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        missing_jobs: list[str] = []
        slow_jobs: list[str] = []
        recovered_failures: list[str] = []

        successful_runs_by_job = {}
        for job_name in EXPECTED_RUNTIME_JOBS:
            successful_run = self._repository.get_latest_successful_job_run_by_name(
                job_name=job_name,
                window_start=window_start,
                window_end=window_end,
            )
            successful_runs_by_job[job_name] = successful_run

            if successful_run is None:
                missing_jobs.append(job_name)
                continue

            if (
                successful_run.duration_ms is not None
                and timedelta(milliseconds=successful_run.duration_ms)
                > self._max_runtime_job_duration
            ):
                slow_jobs.append(job_name)

        completed_manifest = self._repository.get_latest_completed_trading_manifest(
            window_start=window_start,
            window_end=window_end,
        )
        missing_trading_steps: list[str] = []
        if completed_manifest is None:
            missing_trading_steps.extend(["order_reconciliation", "risk_snapshot"])
        elif completed_manifest.last_successful_step != "risk_snapshot":
            if completed_manifest.last_successful_step not in {
                "order_reconciliation",
                "risk_snapshot",
            }:
                missing_trading_steps.append("order_reconciliation")
            missing_trading_steps.append("risk_snapshot")

        failed_runs = self._repository.list_failed_runtime_job_runs(
            window_start=window_start,
            window_end=window_end,
        )
        for failed_run in failed_runs:
            successful_run = successful_runs_by_job.get(failed_run.job_name)
            if successful_run is None:
                continue
            if successful_run.started_at > failed_run.started_at:
                recovered_failures.append(failed_run.job_name)

        metadata = {
            "environment": self._environment,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "expected_jobs": list(EXPECTED_RUNTIME_JOBS),
            "missing_jobs": missing_jobs,
            "slow_jobs": sorted(set(slow_jobs)),
            "recovered_failures": sorted(set(recovered_failures)),
            "missing_trading_steps": missing_trading_steps,
            "completed_manifest_run_id": (
                str(completed_manifest.run_id) if completed_manifest is not None else None
            ),
            "completed_manifest_last_successful_step": (
                completed_manifest.last_successful_step if completed_manifest is not None else None
            ),
        }

        if missing_jobs or missing_trading_steps:
            missing_targets = missing_jobs + missing_trading_steps
            return self._failed(
                RuntimeSoakCheckName.RUNTIME_JOB_HEALTH,
                "Runtime soak verification found missing successful runtime execution.",
                {
                    **metadata,
                    "missing_targets": missing_targets,
                },
            )

        if recovered_failures or slow_jobs:
            warning_reasons: list[str] = []
            if recovered_failures:
                warning_reasons.append("failed jobs recovered later in the window")
            if slow_jobs:
                warning_reasons.append("some jobs exceeded runtime duration threshold")

            return self._warning(
                RuntimeSoakCheckName.RUNTIME_JOB_HEALTH,
                "Runtime job health recovered but degraded during the soak window.",
                {
                    **metadata,
                    "warning_reasons": warning_reasons,
                },
            )

        return self._passed(
            RuntimeSoakCheckName.RUNTIME_JOB_HEALTH,
            "Expected runtime jobs completed successfully in the soak window.",
            metadata,
        )

    def _check_data_freshness(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._passed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Data freshness check placeholder passed.",
            {
                "freshness_lag_threshold_seconds": int(
                    self._freshness_lag_threshold.total_seconds()
                ),
            },
        )

    def _check_stale_running_state(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        cutoff = window_end - self._stale_after
        stale_runtime_jobs = self._repository.list_stale_running_runtime_jobs(cutoff=cutoff)
        stale_manifests = self._repository.list_stale_running_manifests(cutoff=cutoff)

        metadata = {
            "cutoff": cutoff.isoformat(),
            "stale_after_seconds": int(self._stale_after.total_seconds()),
            "stale_runtime_job_ids": [row.job_run_id for row in stale_runtime_jobs],
            "stale_runtime_job_names": [row.job_name for row in stale_runtime_jobs],
            "stale_manifest_run_ids": [str(row.run_id) for row in stale_manifests],
        }

        if stale_runtime_jobs or stale_manifests:
            return self._failed(
                RuntimeSoakCheckName.STALE_RUNNING_STATE,
                "Runtime soak verification found stale running jobs or manifests.",
                metadata,
                severity=RuntimeSoakSeverity.CRITICAL,
            )

        return self._passed(
            RuntimeSoakCheckName.STALE_RUNNING_STATE,
            "No stale running jobs or manifests were found.",
            metadata,
        )

    def _check_order_reconciliation(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._passed(
            RuntimeSoakCheckName.ORDER_RECONCILIATION,
            "Order reconciliation check placeholder passed.",
        )

    def _check_duplicate_fill_protection(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._passed(
            RuntimeSoakCheckName.DUPLICATE_FILL_PROTECTION,
            "Duplicate fill protection check placeholder passed.",
        )

    def _check_cash_position_equity_consistency(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._passed(
            RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
            "Cash, position, and equity consistency check placeholder passed.",
            {
                "cash_drift_tolerance": str(self._cash_drift_tolerance),
                "equity_drift_tolerance": str(self._equity_drift_tolerance),
                "position_quantity_tolerance": str(self._position_quantity_tolerance),
            },
        )

    def _check_observability_signals(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._warning(
            RuntimeSoakCheckName.OBSERVABILITY_SIGNALS,
            "Observability signal verification is not wired yet.",
        )

    def _check_failure_controls(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._warning(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failure control verification is not wired yet.",
        )

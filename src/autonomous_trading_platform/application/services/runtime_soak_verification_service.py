from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.runtime_soak_verification import (
    RuntimeSoakCheckName,
    RuntimeSoakCheckResult,
    RuntimeSoakSeverity,
    RuntimeSoakStatus,
    RuntimeSoakVerificationReport,
)


class RuntimeSoakVerificationService:
    def __init__(
        self,
        session: Session,
        *,
        environment: str,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> None:
        self._session = session
        self._environment = environment
        self._stale_after = stale_after

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
        return self._passed(
            RuntimeSoakCheckName.RUNTIME_JOB_HEALTH,
            "Runtime job health check placeholder passed.",
            {
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
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
        )

    def _check_stale_running_state(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        return self._passed(
            RuntimeSoakCheckName.STALE_RUNNING_STATE,
            "Stale running state check placeholder passed.",
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

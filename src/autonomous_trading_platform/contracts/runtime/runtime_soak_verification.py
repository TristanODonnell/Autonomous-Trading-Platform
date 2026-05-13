from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RuntimeSoakStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class RuntimeSoakSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RuntimeSoakCheckName(StrEnum):
    RUNTIME_JOB_HEALTH = "runtime_job_health"
    DATA_FRESHNESS = "data_freshness"
    STALE_RUNNING_STATE = "stale_running_state"
    CONCURRENT_RUNNING_JOBS = "concurrent_running_jobs"
    ORDER_RECONCILIATION = "order_reconciliation"
    DUPLICATE_FILL_PROTECTION = "duplicate_fill_protection"
    CASH_POSITION_EQUITY_CONSISTENCY = "cash_position_equity_consistency"
    OBSERVABILITY_SIGNALS = "observability_signals"
    FAILURE_CONTROLS = "failure_controls"


class RuntimeSoakCheckResult(BaseModel):
    check_name: RuntimeSoakCheckName
    status: RuntimeSoakStatus
    severity: RuntimeSoakSeverity
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeSoakVerificationReport(BaseModel):
    status: RuntimeSoakStatus
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    window_start: datetime
    window_end: datetime
    environment: str
    checks: list[RuntimeSoakCheckResult]
    summary: dict[str, Any] = Field(default_factory=dict)

    @property
    def failed_checks(self) -> list[RuntimeSoakCheckResult]:
        return [check for check in self.checks if check.status == RuntimeSoakStatus.FAILED]

    @property
    def warning_checks(self) -> list[RuntimeSoakCheckResult]:
        return [check for check in self.checks if check.status == RuntimeSoakStatus.WARNING]

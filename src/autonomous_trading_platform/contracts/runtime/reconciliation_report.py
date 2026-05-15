from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class DriftSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ReconciliationCheckType(StrEnum):
    ORDERS = "orders"
    FILLS = "fills"
    POSITIONS = "positions"
    CASH = "cash"
    EQUITY = "equity"


class ReconciliationStatus(StrEnum):
    PASSED = "passed"
    DRIFTED = "drifted"
    FAILED = "failed"


@dataclass(frozen=True)
class ReconciliationCheckResult:
    check_type: ReconciliationCheckType
    symbol: str | None
    expected_value: Decimal | None
    actual_value: Decimal | None
    delta: Decimal | None
    tolerance: Decimal | None
    severity: DriftSeverity
    status: ReconciliationStatus
    detail: str


@dataclass(frozen=True)
class ReconciliationReport:
    report_id: UUID
    run_id: str
    reconciled_at: datetime
    checks: tuple[ReconciliationCheckResult, ...]
    overall_status: ReconciliationStatus
    unreconciled_order_count: int
    duplicate_fill_count: int

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.reconciliation_report import (
    DriftSeverity,
    ReconciliationCheckResult,
    ReconciliationCheckType,
    ReconciliationReport,
    ReconciliationStatus,
)
from autonomous_trading_platform.observability.metrics import (
    ratp_duplicate_fills_detected_total,
    ratp_equity_drift_amount,
    ratp_unreconciled_orders,
)
from autonomous_trading_platform.storage.sor.repositories.core.broker_account_snapshot_repository import (
    BrokerAccountSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.cash_snapshot_repository import (
    CashSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.position_snapshot_repository import (
    PositionSnapshotRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.tracked_order_repository import (
    TrackedOrderRepository,
)

# ---------------------------------------------------------------------------
# Drift classification thresholds (TASK-622)
# ---------------------------------------------------------------------------

_CASH_WARN_THRESHOLD = Decimal("1.00")
_CASH_CRITICAL_THRESHOLD = Decimal("100.00")

_EQUITY_WARN_THRESHOLD = Decimal("1.00")
_EQUITY_CRITICAL_THRESHOLD = Decimal("100.00")

# Any qty delta < 1 full share is treated as rounding (INFO/DRIFTED).
# >= 1 share is WARNING; >= 10 shares is CRITICAL.
_POSITION_ROUNDING_THRESHOLD = Decimal("1")
_POSITION_CRITICAL_THRESHOLD = Decimal("10")


class BrokerClient(Protocol):
    def get_account(self) -> dict[str, Any]: ...
    def get_positions(self) -> list[dict[str, Any]]: ...
    def list_open_orders(self) -> list[dict[str, Any]]: ...


def _classify_money_drift(
    delta_abs: Decimal,
    warn: Decimal,
    critical: Decimal,
) -> DriftSeverity:
    if delta_abs >= critical:
        return DriftSeverity.CRITICAL
    if delta_abs >= warn:
        return DriftSeverity.WARNING
    return DriftSeverity.INFO


def _classify_qty_drift(delta_abs: Decimal) -> DriftSeverity:
    if delta_abs >= _POSITION_CRITICAL_THRESHOLD:
        return DriftSeverity.CRITICAL
    if delta_abs >= _POSITION_ROUNDING_THRESHOLD:
        return DriftSeverity.WARNING
    return DriftSeverity.INFO


class ExternalBrokerReconciliationService:
    """
    TASK-621 — verifies platform state against live broker state.

    Checks: orders, fills, positions, cash, equity.
    Returns a ReconciliationReport (TASK-623); callers persist it via
    ReconciliationSnapshotRepository.append_report().

    This service performs only reads — it does not mutate any state.
    """

    def __init__(
        self,
        *,
        broker_client: BrokerClient,
        session: Session,
        environment: str = "unknown",
    ) -> None:
        self._broker = broker_client
        self._tracked_orders = TrackedOrderRepository(session)
        self._cash_snapshots = CashSnapshotRepository(session)
        self._position_snapshots = PositionSnapshotRepository(session)
        self._broker_account_snapshots = BrokerAccountSnapshotRepository(session)
        self._environment = environment

    def reconcile(self, run_id: str, *, now: datetime | None = None) -> ReconciliationReport:
        resolved_now = now or datetime.now(UTC)

        broker_account = self._fetch_broker_account()
        broker_open_order_ids = self._fetch_broker_open_order_ids()
        broker_positions = self._fetch_broker_positions()

        checks: list[ReconciliationCheckResult] = []

        order_check, unreconciled_count = self._check_orders(broker_open_order_ids)
        fill_check, duplicate_count = self._check_fills(broker_open_order_ids)
        checks.append(order_check)
        checks.append(fill_check)

        if broker_positions is not None:
            checks.extend(self._check_positions(broker_positions))

        cash_check = self._check_cash(broker_account)
        equity_check = self._check_equity(broker_account)
        checks.append(cash_check)
        checks.append(equity_check)

        self._emit_aggregate_metrics(
            unreconciled_count=unreconciled_count,
            duplicate_count=duplicate_count,
            equity_check=equity_check,
        )

        overall = self._derive_overall_status(checks)
        return ReconciliationReport(
            report_id=uuid4(),
            run_id=run_id,
            reconciled_at=resolved_now,
            checks=tuple(checks),
            overall_status=overall,
            unreconciled_order_count=unreconciled_count,
            duplicate_fill_count=duplicate_count,
        )

    # ------------------------------------------------------------------
    # Broker fetches
    # ------------------------------------------------------------------

    def _fetch_broker_account(self) -> dict[str, Any] | None:
        try:
            return self._broker.get_account()
        except Exception:
            return None

    def _fetch_broker_open_order_ids(self) -> set[str] | None:
        try:
            orders = self._broker.list_open_orders()
            return {o["id"] for o in orders if "id" in o}
        except Exception:
            return None

    def _fetch_broker_positions(self) -> dict[str, Decimal] | None:
        """Returns {symbol: qty} from broker, or None if the call fails."""
        try:
            positions = self._broker.get_positions()
            return {
                p["symbol"]: Decimal(str(p.get("qty", "0"))) for p in positions if "symbol" in p
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _check_orders(
        self,
        broker_open_ids: set[str] | None,
    ) -> tuple[ReconciliationCheckResult, int]:
        if broker_open_ids is None:
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.ORDERS,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=Decimal("0"),
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker open-order fetch failed — order reconciliation skipped.",
            )
            return result, 0

        platform_open = [
            o for o in self._tracked_orders.list_open_orders() if o.broker_order_id is not None
        ]
        unreconciled = [o for o in platform_open if o.broker_order_id not in broker_open_ids]
        count = len(unreconciled)

        if count == 0:
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.ORDERS,
                symbol=None,
                expected_value=Decimal(len(platform_open)),
                actual_value=Decimal(len(platform_open)),
                delta=Decimal("0"),
                tolerance=Decimal("0"),
                severity=DriftSeverity.INFO,
                status=ReconciliationStatus.PASSED,
                detail=f"All {len(platform_open)} platform-open orders confirmed on broker.",
            )
        else:
            severity = DriftSeverity.CRITICAL if count >= 3 else DriftSeverity.WARNING
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.ORDERS,
                symbol=None,
                expected_value=Decimal(len(platform_open)),
                actual_value=Decimal(len(platform_open) - count),
                delta=Decimal(count),
                tolerance=Decimal("0"),
                severity=severity,
                status=ReconciliationStatus.DRIFTED,
                detail=(
                    f"{count} platform-open order(s) absent from broker's open order list: "
                    + ", ".join(o.broker_order_id for o in unreconciled if o.broker_order_id)
                ),
            )
        return result, count

    def _check_fills(
        self,
        broker_open_ids: set[str] | None,
    ) -> tuple[ReconciliationCheckResult, int]:
        """
        Detects possible duplicate fills: platform-side tracked filled qty
        exceeds broker's open order list (orders fully filled that platform
        still tracks as open are the primary signal here — detailed per-order
        comparison requires additional broker API calls, so we use the
        unreconciled-order set as a proxy).
        """
        if broker_open_ids is None:
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.FILLS,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=Decimal("0"),
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker data unavailable — fill duplicate check skipped.",
            )
            return result, 0

        platform_open = self._tracked_orders.list_open_orders()
        possible_duplicates = [
            o
            for o in platform_open
            if o.broker_order_id is not None
            and o.broker_order_id not in broker_open_ids
            and o.previous_filled_qty > Decimal("0")
        ]
        count = len(possible_duplicates)

        if count == 0:
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.FILLS,
                symbol=None,
                expected_value=Decimal("0"),
                actual_value=Decimal("0"),
                delta=Decimal("0"),
                tolerance=Decimal("0"),
                severity=DriftSeverity.INFO,
                status=ReconciliationStatus.PASSED,
                detail="No possible duplicate fill events detected.",
            )
        else:
            result = ReconciliationCheckResult(
                check_type=ReconciliationCheckType.FILLS,
                symbol=None,
                expected_value=Decimal("0"),
                actual_value=Decimal(count),
                delta=Decimal(count),
                tolerance=Decimal("0"),
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.DRIFTED,
                detail=(
                    f"{count} order(s) show fills on our side but are absent from broker's "
                    "open order list — possible duplicate or stale fill records."
                ),
            )
        return result, count

    def _check_positions(
        self,
        broker_positions: dict[str, Decimal],
    ) -> list[ReconciliationCheckResult]:
        latest_snapshot = self._position_snapshots.get_latest()
        if latest_snapshot is None:
            return [
                ReconciliationCheckResult(
                    check_type=ReconciliationCheckType.POSITIONS,
                    symbol=None,
                    expected_value=None,
                    actual_value=None,
                    delta=None,
                    tolerance=Decimal("0"),
                    severity=DriftSeverity.WARNING,
                    status=ReconciliationStatus.FAILED,
                    detail="No internal position snapshot found — position reconciliation skipped.",
                )
            ]

        platform_items = {item.symbol: item.quantity for item in (latest_snapshot.positions or [])}

        all_symbols = set(broker_positions) | set(platform_items)
        results: list[ReconciliationCheckResult] = []

        for symbol in sorted(all_symbols):
            platform_qty = platform_items.get(symbol, Decimal("0"))
            broker_qty = broker_positions.get(symbol, Decimal("0"))
            delta = platform_qty - broker_qty
            delta_abs = abs(delta)
            severity = _classify_qty_drift(delta_abs)
            status = (
                ReconciliationStatus.PASSED
                if delta_abs == Decimal("0")
                else ReconciliationStatus.DRIFTED
            )
            results.append(
                ReconciliationCheckResult(
                    check_type=ReconciliationCheckType.POSITIONS,
                    symbol=symbol,
                    expected_value=platform_qty,
                    actual_value=broker_qty,
                    delta=delta,
                    tolerance=Decimal("0"),
                    severity=severity,
                    status=status,
                    detail=(
                        f"{symbol}: platform={platform_qty}, broker={broker_qty}, delta={delta}"
                    ),
                )
            )

        return results

    def _check_cash(self, broker_account: dict[str, Any] | None) -> ReconciliationCheckResult:
        if broker_account is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.CASH,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=_CASH_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker account fetch failed — cash reconciliation skipped.",
            )

        broker_cash_raw = broker_account.get("cash")
        if broker_cash_raw is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.CASH,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=_CASH_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker account response missing 'cash' field.",
            )

        broker_cash = Decimal(str(broker_cash_raw))
        latest_cash = self._cash_snapshots.get_latest()
        if latest_cash is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.CASH,
                symbol=None,
                expected_value=None,
                actual_value=broker_cash,
                delta=None,
                tolerance=_CASH_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="No internal cash snapshot found — cash reconciliation skipped.",
            )

        platform_cash = latest_cash.cash
        delta = platform_cash - broker_cash
        delta_abs = abs(delta)
        severity = _classify_money_drift(delta_abs, _CASH_WARN_THRESHOLD, _CASH_CRITICAL_THRESHOLD)
        status = (
            ReconciliationStatus.PASSED
            if delta_abs <= _CASH_WARN_THRESHOLD
            else ReconciliationStatus.DRIFTED
        )

        return ReconciliationCheckResult(
            check_type=ReconciliationCheckType.CASH,
            symbol=None,
            expected_value=platform_cash,
            actual_value=broker_cash,
            delta=delta,
            tolerance=_CASH_WARN_THRESHOLD,
            severity=severity,
            status=status,
            detail=(f"Cash: platform={platform_cash}, broker={broker_cash}, delta={delta}"),
        )

    def _check_equity(self, broker_account: dict[str, Any] | None) -> ReconciliationCheckResult:
        if broker_account is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.EQUITY,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=_EQUITY_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker account fetch failed — equity reconciliation skipped.",
            )

        broker_equity_raw = broker_account.get("equity")
        if broker_equity_raw is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.EQUITY,
                symbol=None,
                expected_value=None,
                actual_value=None,
                delta=None,
                tolerance=_EQUITY_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="Broker account response missing 'equity' field.",
            )

        broker_equity = Decimal(str(broker_equity_raw))
        latest_cash = self._cash_snapshots.get_latest()
        if latest_cash is None or latest_cash.equity is None:
            return ReconciliationCheckResult(
                check_type=ReconciliationCheckType.EQUITY,
                symbol=None,
                expected_value=None,
                actual_value=broker_equity,
                delta=None,
                tolerance=_EQUITY_WARN_THRESHOLD,
                severity=DriftSeverity.WARNING,
                status=ReconciliationStatus.FAILED,
                detail="No internal equity snapshot found — equity reconciliation skipped.",
            )

        platform_equity = latest_cash.equity
        delta = platform_equity - broker_equity
        delta_abs = abs(delta)
        severity = _classify_money_drift(
            delta_abs, _EQUITY_WARN_THRESHOLD, _EQUITY_CRITICAL_THRESHOLD
        )
        status = (
            ReconciliationStatus.PASSED
            if delta_abs <= _EQUITY_WARN_THRESHOLD
            else ReconciliationStatus.DRIFTED
        )

        return ReconciliationCheckResult(
            check_type=ReconciliationCheckType.EQUITY,
            symbol=None,
            expected_value=platform_equity,
            actual_value=broker_equity,
            delta=delta,
            tolerance=_EQUITY_WARN_THRESHOLD,
            severity=severity,
            status=status,
            detail=(f"Equity: platform={platform_equity}, broker={broker_equity}, delta={delta}"),
        )

    # ------------------------------------------------------------------
    # Aggregate metric emission
    # ------------------------------------------------------------------

    def _emit_aggregate_metrics(
        self,
        *,
        unreconciled_count: int,
        duplicate_count: int,
        equity_check: ReconciliationCheckResult,
    ) -> None:
        labels = {"environment": self._environment}

        ratp_unreconciled_orders.add(unreconciled_count, labels)

        if duplicate_count > 0:
            ratp_duplicate_fills_detected_total.add(duplicate_count, labels)

        if equity_check.delta is not None:
            ratp_equity_drift_amount.record(float(equity_check.delta), labels)

    # ------------------------------------------------------------------
    # Overall status
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_overall_status(checks: list[ReconciliationCheckResult]) -> ReconciliationStatus:
        if any(c.status == ReconciliationStatus.FAILED for c in checks):
            return ReconciliationStatus.FAILED
        if any(c.status == ReconciliationStatus.DRIFTED for c in checks):
            return ReconciliationStatus.DRIFTED
        return ReconciliationStatus.PASSED

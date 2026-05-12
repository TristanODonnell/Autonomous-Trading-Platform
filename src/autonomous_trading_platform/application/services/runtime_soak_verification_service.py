from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

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
        latest_raw_bars_dataset = self._repository.get_latest_raw_bars_dataset()
        latest_feature_dataset = self._repository.get_latest_feature_dataset()
        latest_trading_manifest = self._repository.get_latest_trading_manifest()

        threshold_seconds = int(self._freshness_lag_threshold.total_seconds())
        raw_bars_lag_seconds = _lag_seconds(
            getattr(latest_raw_bars_dataset, "created_at", None),
            reference_time=window_end,
        )
        feature_dataset_lag_seconds = _lag_seconds(
            getattr(latest_feature_dataset, "created_at", None),
            reference_time=window_end,
        )
        trading_manifest_lag_seconds = _lag_seconds(
            getattr(latest_trading_manifest, "created_at", None),
            reference_time=window_end,
        )

        missing_artifacts: list[str] = []
        stale_artifacts: list[str] = []

        if latest_raw_bars_dataset is None:
            missing_artifacts.append("raw_bars_dataset")
        elif raw_bars_lag_seconds is not None and raw_bars_lag_seconds > threshold_seconds:
            stale_artifacts.append("raw_bars_dataset")

        if latest_feature_dataset is None:
            missing_artifacts.append("feature_dataset")
        elif (
            feature_dataset_lag_seconds is not None
            and feature_dataset_lag_seconds > threshold_seconds
        ):
            stale_artifacts.append("feature_dataset")

        if latest_trading_manifest is None:
            missing_artifacts.append("trading_manifest")
        elif (
            trading_manifest_lag_seconds is not None
            and trading_manifest_lag_seconds > threshold_seconds
        ):
            stale_artifacts.append("trading_manifest")

        metadata = {
            "reference_time": window_end.isoformat(),
            "freshness_lag_threshold_seconds": threshold_seconds,
            "missing_artifacts": missing_artifacts,
            "stale_artifacts": stale_artifacts,
            "raw_bars_dataset": (
                {
                    "dataset_version_id": latest_raw_bars_dataset.dataset_version_id,
                    "created_at": latest_raw_bars_dataset.created_at.isoformat(),
                    "validation_status": latest_raw_bars_dataset.validation_status,
                    "lag_seconds": raw_bars_lag_seconds,
                }
                if latest_raw_bars_dataset is not None
                else None
            ),
            "feature_dataset": (
                {
                    "dataset_version_id": latest_feature_dataset.dataset_version_id,
                    "created_at": latest_feature_dataset.created_at.isoformat(),
                    "validation_status": latest_feature_dataset.validation_status,
                    "lag_seconds": feature_dataset_lag_seconds,
                }
                if latest_feature_dataset is not None
                else None
            ),
            "trading_manifest": (
                {
                    "run_id": str(latest_trading_manifest.run_id),
                    "created_at": latest_trading_manifest.created_at.isoformat(),
                    "status": latest_trading_manifest.status,
                    "last_successful_step": latest_trading_manifest.last_successful_step,
                    "lag_seconds": trading_manifest_lag_seconds,
                }
                if latest_trading_manifest is not None
                else None
            ),
        }

        if missing_artifacts or stale_artifacts:
            return self._failed(
                RuntimeSoakCheckName.DATA_FRESHNESS,
                "Runtime soak verification found missing or stale runtime data artifacts.",
                metadata,
            )

        return self._passed(
            RuntimeSoakCheckName.DATA_FRESHNESS,
            "Latest bars, features, and trading manifests are fresh enough for soak verification.",
            metadata,
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
        cutoff = window_end - self._stale_after
        stale_orders = self._repository.list_stale_submitted_orders_missing_terminal_status(
            cutoff=cutoff
        )
        orders_missing_broker_status = self._repository.list_orders_missing_broker_status(
            window_start=window_start,
            window_end=window_end,
        )
        orders_with_status_mismatch = self._repository.list_orders_with_status_mismatch(
            window_start=window_start,
            window_end=window_end,
        )

        metadata = {
            "cutoff": cutoff.isoformat(),
            "stale_after_seconds": int(self._stale_after.total_seconds()),
            "stale_unreconciled_order_count": len(stale_orders),
            "stale_unreconciled_orders": [_serialize_order(row) for row in stale_orders],
            "orders_missing_broker_status_count": len(orders_missing_broker_status),
            "orders_missing_broker_status": [
                _serialize_order(row) for row in orders_missing_broker_status
            ],
            "status_mismatch_order_count": len(orders_with_status_mismatch),
            "status_mismatch_orders": [
                _serialize_order(row) for row in orders_with_status_mismatch
            ],
        }

        if stale_orders or orders_with_status_mismatch:
            return self._failed(
                RuntimeSoakCheckName.ORDER_RECONCILIATION,
                "Order reconciliation invariants were violated during the soak window.",
                metadata,
            )

        if orders_missing_broker_status:
            return self._warning(
                RuntimeSoakCheckName.ORDER_RECONCILIATION,
                "Order reconciliation could not be fully verified for all orders.",
                metadata,
            )

        return self._passed(
            RuntimeSoakCheckName.ORDER_RECONCILIATION,
            "Order reconciliation checks passed for the soak window.",
            metadata,
        )

    def _check_duplicate_fill_protection(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        duplicate_groups_by_broker_fill_id = (
            self._repository.list_duplicate_fills_by_broker_fill_id(
                window_start=window_start,
                window_end=window_end,
            )
        )
        duplicate_groups_by_order_execution_id = (
            self._repository.list_duplicate_fills_by_order_and_execution_id(
                window_start=window_start,
                window_end=window_end,
            )
        )
        duplicate_groups_by_idempotency_key = (
            self._repository.list_duplicate_fills_by_idempotency_key(
                window_start=window_start,
                window_end=window_end,
            )
        )

        incomplete_detectors = [
            detector_name
            for detector_name, groups in (
                ("broker_fill_id", duplicate_groups_by_broker_fill_id),
                ("order_execution_id", duplicate_groups_by_order_execution_id),
                ("idempotency_key", duplicate_groups_by_idempotency_key),
            )
            if any(not group.fill_ids or group.count < len(group.fill_ids) for group in groups)
        ]

        metadata = {
            "duplicate_groups_by_broker_fill_id": [
                _serialize_duplicate_group(group) for group in duplicate_groups_by_broker_fill_id
            ],
            "duplicate_groups_by_order_execution_id": [
                _serialize_duplicate_group(group)
                for group in duplicate_groups_by_order_execution_id
            ],
            "duplicate_groups_by_idempotency_key": [
                _serialize_duplicate_group(group) for group in duplicate_groups_by_idempotency_key
            ],
            "duplicate_group_counts": {
                "broker_fill_id": len(duplicate_groups_by_broker_fill_id),
                "order_execution_id": len(duplicate_groups_by_order_execution_id),
                "idempotency_key": len(duplicate_groups_by_idempotency_key),
            },
            "incomplete_detectors": incomplete_detectors,
        }

        if (
            duplicate_groups_by_broker_fill_id
            or duplicate_groups_by_order_execution_id
            or duplicate_groups_by_idempotency_key
        ) and not incomplete_detectors:
            return self._failed(
                RuntimeSoakCheckName.DUPLICATE_FILL_PROTECTION,
                "Duplicate fills were detected during runtime soak verification.",
                metadata,
                severity=RuntimeSoakSeverity.CRITICAL,
            )

        if incomplete_detectors:
            return self._warning(
                RuntimeSoakCheckName.DUPLICATE_FILL_PROTECTION,
                "Duplicate fill verification returned incomplete detector metadata.",
                metadata,
            )

        return self._passed(
            RuntimeSoakCheckName.DUPLICATE_FILL_PROTECTION,
            "No duplicate fills were detected.",
            metadata,
        )

    def _check_cash_position_equity_consistency(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeSoakCheckResult:
        latest_cash_snapshot = self._repository.get_latest_cash_snapshot()
        latest_broker_cash_snapshot = self._repository.get_latest_broker_cash_snapshot()
        latest_position_snapshots = self._repository.list_latest_position_snapshots()
        latest_broker_position_snapshots = self._repository.list_latest_broker_position_snapshots()
        latest_portfolio_equity_snapshot = self._repository.get_latest_portfolio_equity_snapshot()
        latest_broker_equity_snapshot = self._repository.get_latest_broker_equity_snapshot()

        ledger_position_snapshot = (
            latest_position_snapshots[0] if latest_position_snapshots else None
        )
        broker_position_snapshot = (
            latest_broker_position_snapshots[0] if latest_broker_position_snapshots else None
        )

        missing_artifacts: list[str] = []
        if latest_cash_snapshot is None:
            missing_artifacts.append("latest_cash_snapshot")
        if latest_broker_cash_snapshot is None:
            missing_artifacts.append("latest_broker_cash_snapshot")
        if ledger_position_snapshot is None:
            missing_artifacts.append("latest_position_snapshot")
        if broker_position_snapshot is None:
            missing_artifacts.append("latest_broker_position_snapshot")
        if latest_portfolio_equity_snapshot is None:
            missing_artifacts.append("latest_portfolio_equity_snapshot")
        if latest_broker_equity_snapshot is None:
            missing_artifacts.append("latest_broker_equity_snapshot")

        cash_drift = _decimal_drift(
            getattr(latest_cash_snapshot, "cash", None),
            getattr(latest_broker_cash_snapshot, "cash", None),
        )
        equity_drift = _decimal_drift(
            getattr(latest_portfolio_equity_snapshot, "equity", None),
            getattr(latest_broker_equity_snapshot, "equity", None),
        )
        position_drift = _position_quantity_drifts(
            ledger_position_snapshot,
            broker_position_snapshot,
        )
        exceeded_position_symbols = [
            symbol
            for symbol, drift in position_drift.items()
            if drift > self._position_quantity_tolerance
        ]

        metadata = {
            "cash_drift_tolerance": str(self._cash_drift_tolerance),
            "equity_drift_tolerance": str(self._equity_drift_tolerance),
            "position_quantity_tolerance": str(self._position_quantity_tolerance),
            "missing_artifacts": missing_artifacts,
            "ledger_cash_snapshot": _serialize_snapshot_identity(latest_cash_snapshot),
            "broker_cash_snapshot": _serialize_snapshot_identity(latest_broker_cash_snapshot),
            "ledger_position_snapshot": _serialize_snapshot_identity(ledger_position_snapshot),
            "broker_position_snapshot": _serialize_snapshot_identity(broker_position_snapshot),
            "ledger_equity_snapshot": _serialize_snapshot_identity(
                latest_portfolio_equity_snapshot
            ),
            "broker_equity_snapshot": _serialize_snapshot_identity(latest_broker_equity_snapshot),
            "cash_drift": str(cash_drift) if cash_drift is not None else None,
            "equity_drift": str(equity_drift) if equity_drift is not None else None,
            "position_quantity_drift": {
                symbol: str(drift) for symbol, drift in sorted(position_drift.items())
            },
            "exceeded_position_symbols": exceeded_position_symbols,
        }

        if missing_artifacts:
            return self._warning(
                RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
                "Cash, position, and equity consistency could not be fully verified.",
                metadata,
            )

        if (
            cash_drift is not None
            and cash_drift > self._cash_drift_tolerance
            or equity_drift is not None
            and equity_drift > self._equity_drift_tolerance
            or exceeded_position_symbols
        ):
            return self._failed(
                RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
                "Cash, position, or equity drift exceeded configured tolerances.",
                metadata,
            )

        return self._passed(
            RuntimeSoakCheckName.CASH_POSITION_EQUITY_CONSISTENCY,
            "Cash, position, and equity snapshots reconciled within tolerance.",
            metadata,
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
        failed_runtime_jobs = self._repository.list_failed_runtime_job_runs(
            window_start=window_start,
            window_end=window_end,
        )
        failed_manifests = self._repository.list_failed_manifests(
            window_start=window_start,
            window_end=window_end,
        )
        failure_audit_events = self._repository.list_failure_audit_events(
            window_start=window_start,
            window_end=window_end,
        )
        runtime_control_state = self._repository.get_current_runtime_control_state()
        latest_risk_snapshot = self._repository.get_latest_risk_snapshot()
        trading_freeze_state = self._repository.get_current_trading_freeze_state()

        runtime_control_evidence = bool(
            runtime_control_state is not None
            and (
                runtime_control_state.kill_switch_enabled
                or runtime_control_state.trading_paused
                or not runtime_control_state.trading_enabled
                or runtime_control_state.reason is not None
            )
        )
        risk_block_evidence = bool(
            latest_risk_snapshot is not None and latest_risk_snapshot.is_blocked
        )

        metadata = {
            "failed_runtime_job_count": len(failed_runtime_jobs),
            "failed_runtime_jobs": [
                {
                    "job_run_id": row.job_run_id,
                    "job_name": row.job_name,
                    "started_at": row.started_at.isoformat(),
                    "error_message": row.error_message,
                }
                for row in failed_runtime_jobs
            ],
            "failed_manifest_count": len(failed_manifests),
            "failed_manifest_run_ids": [str(row.run_id) for row in failed_manifests],
            "failure_audit_event_count": len(failure_audit_events),
            "failure_audit_event_types": [row.event_type for row in failure_audit_events],
            "runtime_control_evidence": runtime_control_evidence,
            "risk_block_evidence": risk_block_evidence,
            "runtime_control_state": (
                {
                    "trading_enabled": runtime_control_state.trading_enabled,
                    "trading_paused": runtime_control_state.trading_paused,
                    "kill_switch_enabled": runtime_control_state.kill_switch_enabled,
                    "reason": runtime_control_state.reason,
                    "updated_at": runtime_control_state.updated_at.isoformat(),
                }
                if runtime_control_state is not None
                else None
            ),
            "latest_risk_snapshot": (
                {
                    "snapshot_id": str(latest_risk_snapshot.snapshot_id),
                    "timestamp": latest_risk_snapshot.timestamp.isoformat(),
                    "is_blocked": latest_risk_snapshot.is_blocked,
                    "block_reasons": latest_risk_snapshot.block_reasons,
                }
                if latest_risk_snapshot is not None
                else None
            ),
            "trading_freeze_persisted": trading_freeze_state is not None,
        }

        if not failed_runtime_jobs:
            return self._passed(
                RuntimeSoakCheckName.FAILURE_CONTROLS,
                "No failed runtime execution required failure-control escalation.",
                metadata,
            )

        missing_artifacts: list[str] = []
        if not failed_manifests:
            missing_artifacts.append("failed_manifest")
        if not failure_audit_events:
            missing_artifacts.append("failure_audit_event")
        if not runtime_control_evidence and not risk_block_evidence:
            missing_artifacts.append("runtime_control_or_risk_block_evidence")

        if missing_artifacts:
            return self._failed(
                RuntimeSoakCheckName.FAILURE_CONTROLS,
                "Failed runtime execution did not leave the expected failure-control artifacts.",
                {
                    **metadata,
                    "missing_failure_control_artifacts": missing_artifacts,
                },
            )

        if trading_freeze_state is None:
            return self._warning(
                RuntimeSoakCheckName.FAILURE_CONTROLS,
                "Failure controls were observed, but trading freeze persistence is not wired.",
                {
                    **metadata,
                    "missing_failure_control_artifacts": ["trading_freeze_persistence"],
                },
            )

        return self._passed(
            RuntimeSoakCheckName.FAILURE_CONTROLS,
            "Failed runtime execution left the expected failure-control artifacts.",
            metadata,
        )


def _serialize_order(row: Any) -> dict[str, Any]:
    return {
        "broker_order_id": row.broker_order_id,
        "client_order_id": row.client_order_id,
        "status": str(row.status),
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at is not None else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at is not None else None,
        "filled_qty": str(row.filled_qty),
        "last_error": row.last_error,
    }


def _serialize_duplicate_group(group: Any) -> dict[str, Any]:
    return {
        "group_key": group.group_key,
        "fill_ids": list(group.fill_ids),
        "count": group.count,
    }


def _serialize_snapshot_identity(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    snapshot_id = getattr(row, "snapshot_id", None)
    timestamp = getattr(row, "timestamp", None)
    return {
        "snapshot_id": str(snapshot_id) if snapshot_id is not None else None,
        "timestamp": timestamp.isoformat() if timestamp is not None else None,
    }


def _decimal_drift(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return abs(Decimal(left) - Decimal(right))


def _position_quantity_drifts(
    ledger_snapshot: Any,
    broker_snapshot: Any,
) -> dict[str, Decimal]:
    if ledger_snapshot is None or broker_snapshot is None:
        return {}

    ledger_positions = _position_quantities_by_symbol(ledger_snapshot)
    broker_positions = _position_quantities_by_symbol(broker_snapshot)

    drifts: dict[str, Decimal] = {}
    for symbol in sorted(set(ledger_positions) | set(broker_positions)):
        drifts[symbol] = abs(
            ledger_positions.get(symbol, Decimal("0")) - broker_positions.get(symbol, Decimal("0"))
        )
    return drifts


def _position_quantities_by_symbol(snapshot: Any) -> dict[str, Decimal]:
    positions = getattr(snapshot, "positions", [])
    return {item.symbol: Decimal(item.quantity) for item in positions}


def _lag_seconds(
    artifact_time: datetime | None,
    *,
    reference_time: datetime,
) -> int | None:
    if artifact_time is None:
        return None
    return max(0, int((reference_time - artifact_time).total_seconds()))

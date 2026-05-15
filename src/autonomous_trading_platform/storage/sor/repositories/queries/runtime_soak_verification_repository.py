from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import asc, case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from autonomous_trading_platform.contracts.common.enums import OrderSource, OrderStatus
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.models.feature_dataset_versions import (
    FeatureDatasetVersions,
)
from autonomous_trading_platform.storage.sor.models.fills import Fill
from autonomous_trading_platform.storage.sor.models.order_intents import OrderIntents
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.models.reconciliation_snapshots import (
    ReconciliationSnapshot,
)
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.models.run_manifests import RunManifestRow
from autonomous_trading_platform.storage.sor.models.runtime_control_state import (
    RuntimeControlState,
)
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance

FINALIZED_DATASET_STATUSES = ("validated", "complete", "finalized")
TERMINAL_ORDER_STATUSES = (
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
)


@dataclass(frozen=True)
class DuplicateFillGroup:
    group_key: str
    fill_ids: list[str]
    count: int


class RuntimeSoakVerificationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # TASK-506
    # Runtime job health

    def list_runtime_job_runs(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.started_at >= window_start,
                RuntimeJobRuns.started_at <= window_end,
            )
            .order_by(RuntimeJobRuns.started_at.asc(), RuntimeJobRuns.job_name.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_failed_runtime_job_runs(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.status == "failed",
                RuntimeJobRuns.started_at >= window_start,
                RuntimeJobRuns.started_at <= window_end,
            )
            .order_by(RuntimeJobRuns.started_at.asc(), RuntimeJobRuns.job_name.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_latest_successful_job_run_by_name(
        self,
        *,
        job_name: str,
        window_start: datetime,
        window_end: datetime,
    ) -> RuntimeJobRuns | None:
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.job_name == job_name,
                RuntimeJobRuns.status == "completed",
                RuntimeJobRuns.started_at >= window_start,
                RuntimeJobRuns.started_at <= window_end,
            )
            .order_by(desc(RuntimeJobRuns.completed_at), desc(RuntimeJobRuns.started_at))
            .limit(1)
        )
        return cast(RuntimeJobRuns | None, self.session.scalars(stmt).one_or_none())

    def get_latest_completed_trading_manifest(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> RunManifestRow | None:
        stmt = (
            select(RunManifestRow)
            .where(
                RunManifestRow.created_at >= window_start,
                RunManifestRow.created_at <= window_end,
                RunManifestRow.status == "completed",
            )
            .order_by(desc(RunManifestRow.created_at))
            .limit(1)
        )
        return cast(RunManifestRow | None, self.session.scalars(stmt).one_or_none())

    # TASK-507
    # Data freshness

    def get_latest_raw_bars_dataset(self) -> DatasetVersions | None:
        stmt = (
            select(DatasetVersions)
            .where(DatasetVersions.validation_status.in_(FINALIZED_DATASET_STATUSES))
            .order_by(desc(DatasetVersions.created_at))
            .limit(1)
        )
        return cast(DatasetVersions | None, self.session.scalars(stmt).one_or_none())

    def get_latest_feature_dataset(self) -> FeatureDatasetVersions | None:
        stmt = (
            select(FeatureDatasetVersions)
            .where(FeatureDatasetVersions.validation_status.in_(FINALIZED_DATASET_STATUSES))
            .order_by(desc(FeatureDatasetVersions.created_at))
            .limit(1)
        )
        return cast(FeatureDatasetVersions | None, self.session.scalars(stmt).one_or_none())

    def get_latest_trading_manifest(self) -> RunManifestRow | None:
        stmt = select(RunManifestRow).order_by(desc(RunManifestRow.created_at)).limit(1)
        return cast(RunManifestRow | None, self.session.scalars(stmt).one_or_none())

    # TASK-508
    # Stale running state / concurrent execution detection

    def list_concurrent_running_job_names(self) -> list[str]:
        """Return job names that currently have 2 or more active RUNNING records.

        Any entry here indicates the no-overlap lock did not prevent concurrent
        execution of the same job — a critical invariant violation.
        """
        subq = (
            select(RuntimeJobRuns.job_name)
            .where(RuntimeJobRuns.status == "running")
            .group_by(RuntimeJobRuns.job_name)
            .having(func.count(RuntimeJobRuns.job_run_id) >= 2)
        )
        return list(self.session.scalars(subq).all())

    def list_stale_running_runtime_jobs(
        self,
        *,
        cutoff: datetime,
    ) -> list[RuntimeJobRuns]:
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.status == "running",
                RuntimeJobRuns.started_at < cutoff,
            )
            .order_by(RuntimeJobRuns.started_at.asc(), RuntimeJobRuns.job_name.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_stale_running_manifests(
        self,
        *,
        cutoff: datetime,
    ) -> list[RunManifestRow]:
        stmt = (
            select(RunManifestRow)
            .where(
                RunManifestRow.status == "running",
                RunManifestRow.created_at < cutoff,
            )
            .order_by(RunManifestRow.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    # TASK-509
    # Order reconciliation

    def list_stale_submitted_orders_missing_terminal_status(
        self,
        *,
        cutoff: datetime,
    ) -> list[BrokerOrder]:
        stale_timestamp = func.coalesce(BrokerOrder.updated_at, BrokerOrder.submitted_at)
        stmt = (
            select(BrokerOrder)
            .where(
                BrokerOrder.status.not_in(TERMINAL_ORDER_STATUSES),
                stale_timestamp < cutoff,
            )
            .order_by(BrokerOrder.updated_at.asc(), BrokerOrder.submitted_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_orders_missing_broker_status(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BrokerOrder]:
        stmt = (
            select(BrokerOrder)
            .where(
                BrokerOrder.submitted_at.is_not(None),
                BrokerOrder.submitted_at >= window_start,
                BrokerOrder.submitted_at <= window_end,
                BrokerOrder.raw_broker_payload.is_(None),
                BrokerOrder.status == OrderStatus.SUBMITTED,
            )
            .order_by(BrokerOrder.submitted_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_orders_with_status_mismatch(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[BrokerOrder]:
        # No canonical separate platform-status table exists yet. Return the
        # subset that still looks suspicious for the service to reason about.
        stmt = (
            select(BrokerOrder)
            .where(
                BrokerOrder.submitted_at.is_not(None),
                BrokerOrder.submitted_at >= window_start,
                BrokerOrder.submitted_at <= window_end,
                BrokerOrder.status == OrderStatus.PARTIALLY_FILLED,
                BrokerOrder.filled_qty == Decimal("0"),
            )
            .order_by(BrokerOrder.submitted_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    # TASK-510
    # Duplicate fill protection

    def list_duplicate_fills_by_broker_fill_id(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[DuplicateFillGroup]:
        fills = self._list_fills_in_window(window_start=window_start, window_end=window_end)
        return self._group_duplicate_fills(
            fills,
            lambda fill: _json_value(fill.meta, "broker_fill_id"),
        )

    def list_duplicate_fills_by_order_and_execution_id(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[DuplicateFillGroup]:
        fills = self._list_fills_in_window(window_start=window_start, window_end=window_end)
        return self._group_duplicate_fills(
            fills,
            lambda fill: _compound_key(
                fill.broker_order_id,
                _json_value(fill.meta, "execution_id"),
            ),
        )

    def list_duplicate_fills_by_idempotency_key(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[DuplicateFillGroup]:
        stmt = (
            select(Fill, OrderIntents.idempotency_key)
            .join(OrderIntents, OrderIntents.intent_id == Fill.intent_id)
            .where(Fill.timestamp >= window_start, Fill.timestamp <= window_end)
        )

        grouped: dict[str, list[str]] = {}
        for fill, idempotency_key in self.session.execute(stmt).all():
            if not idempotency_key:
                continue
            grouped.setdefault(str(idempotency_key), []).append(fill.fill_id)

        return [
            DuplicateFillGroup(group_key=key, fill_ids=fill_ids, count=len(fill_ids))
            for key, fill_ids in sorted(grouped.items())
            if len(fill_ids) > 1
        ]

    # TASK-511
    # Cash / position / equity consistency

    def get_latest_cash_snapshot(self) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .order_by(
                desc(CashSnapshot.timestamp),
                asc(_cash_source_priority()),
                desc(CashSnapshot.snapshot_id),
            )
            .limit(1)
        )
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest_broker_cash_snapshot(self) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.source == OrderSource.BROKER_RECONCILED)
            .order_by(desc(CashSnapshot.timestamp), desc(CashSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def list_latest_position_snapshots(self) -> list[PositionSnapshot]:
        snapshot = self._latest_position_snapshot(source=OrderSource.LEDGER)
        return [] if snapshot is None else [snapshot]

    def list_latest_broker_position_snapshots(self) -> list[PositionSnapshot]:
        snapshot = self._latest_position_snapshot(source=OrderSource.BROKER_RECONCILED)
        return [] if snapshot is None else [snapshot]

    def get_latest_portfolio_equity_snapshot(self) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(
                CashSnapshot.source == OrderSource.LEDGER,
                CashSnapshot.equity.is_not(None),
            )
            .order_by(desc(CashSnapshot.timestamp), desc(CashSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest_broker_equity_snapshot(self) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(
                CashSnapshot.source == OrderSource.BROKER_RECONCILED,
                CashSnapshot.equity.is_not(None),
            )
            .order_by(desc(CashSnapshot.timestamp), desc(CashSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    # TASK-512
    # Observability signal existence

    def list_recent_cycle_run_ids(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[str]:
        stmt = (
            select(RunManifestRow.run_id)
            .where(
                RunManifestRow.created_at >= window_start,
                RunManifestRow.created_at <= window_end,
            )
            .order_by(RunManifestRow.created_at.desc())
        )
        return [str(run_id) for run_id in self.session.scalars(stmt).all()]

    def list_recent_incident_events(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[AuditLogRow]:
        stmt = (
            select(AuditLogRow)
            .where(
                AuditLogRow.event_timestamp >= window_start,
                AuditLogRow.event_timestamp <= window_end,
                or_(
                    AuditLogRow.event_type.like("%FAILED%"),
                    AuditLogRow.event_type.like("%BREACH%"),
                    AuditLogRow.event_type.like("%FROZEN%"),
                ),
            )
            .order_by(AuditLogRow.event_timestamp.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_audit_events_by_component_or_type(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        component_prefixes: tuple[str, ...] = (),
        event_type_patterns: tuple[str, ...] = (),
    ) -> list[AuditLogRow]:
        predicates = []
        for prefix in component_prefixes:
            predicates.append(AuditLogRow.component.like(f"{prefix}%"))
        for pattern in event_type_patterns:
            predicates.append(AuditLogRow.event_type.like(pattern))
        if not predicates:
            return []

        stmt = (
            select(AuditLogRow)
            .where(
                AuditLogRow.event_timestamp >= window_start,
                AuditLogRow.event_timestamp <= window_end,
                or_(*predicates),
            )
            .order_by(AuditLogRow.event_timestamp.asc())
        )
        return list(self.session.scalars(stmt).all())

    def count_reconciliation_snapshots(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        stmt = select(func.count(ReconciliationSnapshot.snapshot_id)).where(
            ReconciliationSnapshot.reconciled_at >= window_start,
            ReconciliationSnapshot.reconciled_at <= window_end,
        )
        return int(self.session.scalar(stmt) or 0)

    def list_governance_transitions(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[StrategyGovernance]:
        stmt = (
            select(StrategyGovernance)
            .where(
                StrategyGovernance.updated_at >= window_start,
                StrategyGovernance.updated_at <= window_end,
            )
            .order_by(StrategyGovernance.updated_at.asc(), StrategyGovernance.strategy_id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_successful_job_runs_by_names(
        self,
        *,
        job_names: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RuntimeJobRuns]:
        if not job_names:
            return []
        stmt = (
            select(RuntimeJobRuns)
            .where(
                RuntimeJobRuns.job_name.in_(job_names),
                RuntimeJobRuns.status == "completed",
                RuntimeJobRuns.started_at >= window_start,
                RuntimeJobRuns.started_at <= window_end,
            )
            .order_by(RuntimeJobRuns.started_at.asc(), RuntimeJobRuns.job_name.asc())
        )
        return list(self.session.scalars(stmt).all())

    # TASK-513
    # Failure controls

    def list_failed_manifests(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RunManifestRow]:
        stmt = (
            select(RunManifestRow)
            .where(
                RunManifestRow.status == "failed",
                RunManifestRow.created_at >= window_start,
                RunManifestRow.created_at <= window_end,
            )
            .order_by(RunManifestRow.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_failure_audit_events(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[AuditLogRow]:
        stmt = (
            select(AuditLogRow)
            .where(
                AuditLogRow.event_timestamp >= window_start,
                AuditLogRow.event_timestamp <= window_end,
                or_(
                    AuditLogRow.event_type == "RUN_FAILED",
                    AuditLogRow.event_type == "SLA_BREACH",
                    AuditLogRow.event_type.like("%FAILED%"),
                ),
            )
            .order_by(AuditLogRow.event_timestamp.asc())
        )
        return list(self.session.scalars(stmt).all())

    def get_current_runtime_control_state(self) -> RuntimeControlState | None:
        stmt = (
            select(RuntimeControlState).where(RuntimeControlState.control_id == "global").limit(1)
        )
        return cast(RuntimeControlState | None, self.session.scalars(stmt).one_or_none())

    def get_current_trading_freeze_state(self) -> Any | None:
        # No persisted trading freeze model exists yet.
        return None

    def get_latest_risk_snapshot(self) -> RiskSnapshot | None:
        stmt = select(RiskSnapshot).order_by(desc(RiskSnapshot.timestamp)).limit(1)
        return cast(RiskSnapshot | None, self.session.scalars(stmt).one_or_none())

    def _list_fills_in_window(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Fill]:
        stmt = (
            select(Fill)
            .where(Fill.timestamp >= window_start, Fill.timestamp <= window_end)
            .order_by(Fill.timestamp.asc(), Fill.fill_id.asc())
        )
        return list(self.session.scalars(stmt).all())

    def _group_duplicate_fills(
        self,
        fills: list[Fill],
        key_builder,
    ) -> list[DuplicateFillGroup]:
        grouped: dict[str, list[str]] = {}

        for fill in fills:
            key = key_builder(fill)
            if not key:
                continue
            grouped.setdefault(str(key), []).append(fill.fill_id)

        return [
            DuplicateFillGroup(group_key=key, fill_ids=fill_ids, count=len(fill_ids))
            for key, fill_ids in sorted(grouped.items())
            if len(fill_ids) > 1
        ]

    def _latest_position_snapshot(
        self,
        *,
        source: OrderSource,
    ) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .options(selectinload(PositionSnapshot.positions))
            .where(PositionSnapshot.source == source)
            .order_by(desc(PositionSnapshot.timestamp), desc(PositionSnapshot.snapshot_id))
            .limit(1)
        )
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())


def _json_value(payload: dict[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None

    value = payload.get(key)
    if value is None:
        return None

    return str(value)


def _compound_key(left: str | None, right: str | None) -> str | None:
    if not left or not right:
        return None
    return f"{left}::{right}"


def _cash_source_priority():
    return case(
        (CashSnapshot.source == OrderSource.LEDGER, 0),
        (CashSnapshot.source == OrderSource.BROKER_RECONCILED, 1),
        else_=2,
    )

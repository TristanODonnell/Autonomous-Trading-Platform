from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, select

from autonomous_trading_platform.contracts.governance.governance_audit import (
    GovernanceAuditRecord,
)
from autonomous_trading_platform.storage.sor.models.governance_audit_events import (
    GovernanceAuditEventRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class GovernanceAuditRepository(BaseRepository):
    def record(self, row: GovernanceAuditEventRow) -> None:
        self.session.add(row)

    def get_by_id(self, governance_audit_id: str) -> GovernanceAuditEventRow | None:
        return cast(
            GovernanceAuditEventRow | None,
            self.session.get(GovernanceAuditEventRow, governance_audit_id),
        )

    def list_for_strategy(
        self,
        strategy_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[GovernanceAuditEventRow], int]:
        stmt = select(GovernanceAuditEventRow).where(
            GovernanceAuditEventRow.strategy_id == strategy_id
        )
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.session.scalars(
                stmt.order_by(GovernanceAuditEventRow.recorded_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def list_by_trigger_source(
        self,
        trigger_source: str,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[GovernanceAuditEventRow], int]:
        stmt = select(GovernanceAuditEventRow).where(
            GovernanceAuditEventRow.trigger_source == trigger_source
        )
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.session.scalars(
                stmt.order_by(GovernanceAuditEventRow.recorded_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def list_events(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        strategy_id: str | None = None,
        trigger_source: str | None = None,
        event_type: str | None = None,
        decision_outcome: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> tuple[list[GovernanceAuditEventRow], int]:
        stmt = select(GovernanceAuditEventRow)
        if strategy_id is not None:
            stmt = stmt.where(GovernanceAuditEventRow.strategy_id == strategy_id)
        if trigger_source is not None:
            stmt = stmt.where(GovernanceAuditEventRow.trigger_source == trigger_source)
        if event_type is not None:
            stmt = stmt.where(GovernanceAuditEventRow.event_type == event_type)
        if decision_outcome is not None:
            stmt = stmt.where(GovernanceAuditEventRow.decision_outcome == decision_outcome)
        if from_date is not None:
            stmt = stmt.where(GovernanceAuditEventRow.recorded_at >= from_date)
        if to_date is not None:
            stmt = stmt.where(GovernanceAuditEventRow.recorded_at <= to_date)

        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = list(
            self.session.scalars(
                stmt.order_by(GovernanceAuditEventRow.recorded_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )
        return rows, total

    def mark_superseded(
        self, governance_audit_id: str, *, superseded_by: str
    ) -> GovernanceAuditEventRow | None:
        row = self.get_by_id(governance_audit_id)
        if row is None:
            return None
        row.superseded_by = superseded_by
        return row

    def get_supersession_chain(self, governance_audit_id: str) -> list[GovernanceAuditEventRow]:
        """Follow superseded_by links from the given id back to the root."""
        chain: list[GovernanceAuditEventRow] = []
        current_id: str | None = governance_audit_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            row = self.get_by_id(current_id)
            if row is None:
                break
            chain.append(row)
            current_id = row.superseded_by
        return chain

    @staticmethod
    def row_to_record(row: GovernanceAuditEventRow) -> GovernanceAuditRecord:
        return GovernanceAuditRecord(
            governance_audit_id=row.governance_audit_id,
            strategy_id=row.strategy_id,
            event_type=row.event_type,
            trigger_source=row.trigger_source,
            transition_from=row.transition_from,
            transition_to=row.transition_to,
            actor=row.actor,
            decision_outcome=row.decision_outcome,
            decision_rationale=row.decision_rationale or "",
            criteria_evaluated=row.criteria_evaluated or [],
            metrics_lineage=row.metrics_lineage or {},
            state_snapshot_before=row.state_snapshot_before or {},
            state_snapshot_after=row.state_snapshot_after or {},
            source_run_id=row.source_run_id,
            simulation_run_id=row.simulation_run_id,
            experiment_run_id=row.experiment_run_id,
            allocation_config_hash=row.allocation_config_hash,
            covariance_snapshot_id=row.covariance_snapshot_id,
            factor_snapshot_id=row.factor_snapshot_id,
            promotion_rule_version=row.promotion_rule_version,
            governance_policy_version=row.governance_policy_version,
            evaluation_version=row.evaluation_version,
            shadow_validation_status=row.shadow_validation_status,
            shadow_divergence_metrics=row.shadow_divergence_metrics,
            superseded_by=row.superseded_by,
            evaluation_run_id=row.evaluation_run_id,
            recorded_at=row.recorded_at,
        )

    @staticmethod
    def _build_row(
        governance_audit_id: str,
        strategy_id: str,
        event_type: str,
        trigger_source: str,
        actor: str,
        decision_outcome: str,
        recorded_at: datetime,
        *,
        transition_from: str | None = None,
        transition_to: str | None = None,
        decision_rationale: str | None = None,
        criteria_evaluated: list[dict[str, Any]] | None = None,
        metrics_lineage: dict[str, Any] | None = None,
        state_snapshot_before: dict[str, Any] | None = None,
        state_snapshot_after: dict[str, Any] | None = None,
        source_run_id: str | None = None,
        simulation_run_id: str | None = None,
        experiment_run_id: str | None = None,
        allocation_config_hash: str | None = None,
        covariance_snapshot_id: str | None = None,
        factor_snapshot_id: str | None = None,
        promotion_rule_version: str | None = None,
        governance_policy_version: str | None = None,
        evaluation_version: str | None = None,
        shadow_validation_status: str | None = None,
        shadow_divergence_metrics: dict[str, Any] | None = None,
        superseded_by: str | None = None,
        evaluation_run_id: str | None = None,
    ) -> GovernanceAuditEventRow:
        return GovernanceAuditEventRow(
            governance_audit_id=governance_audit_id,
            strategy_id=strategy_id,
            event_type=event_type,
            trigger_source=trigger_source,
            transition_from=transition_from,
            transition_to=transition_to,
            actor=actor,
            decision_outcome=decision_outcome,
            decision_rationale=decision_rationale,
            criteria_evaluated=criteria_evaluated,
            metrics_lineage=metrics_lineage,
            state_snapshot_before=state_snapshot_before,
            state_snapshot_after=state_snapshot_after,
            source_run_id=source_run_id,
            simulation_run_id=simulation_run_id,
            experiment_run_id=experiment_run_id,
            allocation_config_hash=allocation_config_hash,
            covariance_snapshot_id=covariance_snapshot_id,
            factor_snapshot_id=factor_snapshot_id,
            promotion_rule_version=promotion_rule_version,
            governance_policy_version=governance_policy_version,
            evaluation_version=evaluation_version,
            shadow_validation_status=shadow_validation_status,
            shadow_divergence_metrics=shadow_divergence_metrics,
            superseded_by=superseded_by,
            evaluation_run_id=evaluation_run_id,
            recorded_at=recorded_at,
        )

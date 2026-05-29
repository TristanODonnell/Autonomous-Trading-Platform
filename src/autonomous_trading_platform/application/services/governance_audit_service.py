from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.governance.governance_audit import (
    DecisionOutcome,
    GovernanceAuditListResult,
    GovernanceAuditRecord,
    GovernanceCriteriaEvaluation,
    GovernanceDecisionEvidence,
    TriggerSource,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.observability.metrics import (
    governance_auto_actions_total,
    governance_decisions_total,
    governance_demotions_total,
    governance_promotions_total,
    governance_superseded_events_total,
)
from autonomous_trading_platform.storage.sor.models.governance_audit_events import (
    GovernanceAuditEventRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.audit_logs_repository import (
    AuditLogRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.governance_audit_repository import (
    GovernanceAuditRepository,
)

logger = get_logger(__name__)

# Governance audit policy version - bump when promotion/demotion rule logic changes
GOVERNANCE_POLICY_VERSION = "1.0.0"
EVALUATION_VERSION = "1.0.0"


class GovernanceAuditService:
    """
    Records complete, machine-readable governance decision evidence.

    Every governance action - promotion, demotion, health lifecycle transition,
    drawdown ladder escalation, operator override - must produce one audit event
    here. The record captures what happened, why it happened, what evidence was
    evaluated, and which data run was used so that future operators can fully
    reconstruct the decision years later.
    """

    def __init__(
        self,
        *,
        session: Session,
        repo: GovernanceAuditRepository | None = None,
        audit_log_repo: AuditLogRepository | None = None,
    ) -> None:
        self._session = session
        self._repo = repo or GovernanceAuditRepository(session)
        self._audit_log_repo = audit_log_repo or AuditLogRepository(session)

    # ------------------------------------------------------------------
    # Recording API - called by promotion/demotion/health/drawdown services
    # ------------------------------------------------------------------

    def record_promotion_decision(
        self,
        *,
        strategy_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        trigger_source: TriggerSource,
        eligible: bool,
        criteria: list[GovernanceCriteriaEvaluation],
        metrics_snapshot: dict[str, Any],
        source_run_id: str | None,
        rule_id: str | None,
        reasons: list[str],
        evaluation_run_id: str | None = None,
        now: datetime | None = None,
    ) -> GovernanceAuditEventRow:
        now = now or datetime.now(UTC)
        outcome = DecisionOutcome.APPROVED if eligible else DecisionOutcome.REJECTED
        if (
            _requires_source_run(to_state=to_state, trigger_source=trigger_source)
            and not source_run_id
        ):
            self._emit_observability_event(
                "MISSING_GOVERNANCE_EVIDENCE",
                strategy_id=strategy_id,
                transition=f"{from_state}->{to_state}",
                trigger_source=trigger_source,
                source_run_id=source_run_id,
                criteria_count=len(criteria),
                decision_outcome=str(outcome),
                occurred_at=now,
            )

        passed = [c for c in criteria if c.passed and c.threshold is not None]
        failed = [c for c in criteria if not c.passed and c.threshold is not None]
        rationale = _build_promotion_rationale(
            from_state=from_state,
            to_state=to_state,
            criteria=criteria,
            eligible=eligible,
            reasons=reasons,
        )
        evidence = GovernanceDecisionEvidence(
            governance_state_before=from_state,
            governance_state_after=to_state if eligible else from_state,
            criteria_evaluated=criteria,
            metrics_snapshot=metrics_snapshot,
            source_run_id=source_run_id,
            promotion_rule_version=rule_id,
            governance_policy_version=GOVERNANCE_POLICY_VERSION,
            evaluation_version=EVALUATION_VERSION,
        )
        row = self._build_and_record(
            strategy_id=strategy_id,
            event_type=(
                "GOVERNANCE_PROMOTION_APPROVED" if eligible else "GOVERNANCE_PROMOTION_REJECTED"
            ),
            trigger_source=trigger_source,
            actor=actor,
            decision_outcome=outcome,
            decision_rationale=rationale,
            evidence=evidence,
            evaluation_run_id=evaluation_run_id,
            now=now,
        )

        _labels = {"trigger_source": trigger_source, "strategy_id": strategy_id}
        governance_decisions_total.add(1, _labels)
        governance_promotions_total.add(1, _labels)
        if trigger_source in (TriggerSource.AUTO_PROMOTION,):
            governance_auto_actions_total.add(1, _labels)

        logger.info(
            "PROMOTION_EVIDENCE_RECORDED",
            extra={
                "governance_audit_id": row.governance_audit_id,
                "strategy_id": strategy_id,
                "from_state": from_state,
                "to_state": to_state,
                "eligible": eligible,
                "trigger_source": trigger_source,
                "criteria_count": len(criteria),
                "passed_count": len(passed),
                "failed_count": len(failed),
                "source_run_id": source_run_id,
            },
        )
        self._emit_observability_event(
            "GOVERNANCE_DECISION_RECORDED",
            strategy_id=strategy_id,
            transition=f"{from_state}->{to_state}",
            trigger_source=trigger_source,
            source_run_id=source_run_id,
            criteria_count=len(criteria),
            decision_outcome=str(outcome),
            occurred_at=now,
        )
        self._emit_observability_event(
            "PROMOTION_EVIDENCE_RECORDED",
            strategy_id=strategy_id,
            transition=f"{from_state}->{to_state}",
            trigger_source=trigger_source,
            source_run_id=source_run_id,
            criteria_count=len(criteria),
            decision_outcome=str(outcome),
            occurred_at=now,
        )
        return row

    def record_demotion_decision(
        self,
        *,
        strategy_id: str,
        from_state: str,
        to_state: str | None,
        actor: str,
        trigger_source: TriggerSource,
        breach: bool,
        breached_metric: str | None,
        breached_value: float | None,
        threshold: float | None,
        source_id: str | None,
        source_type: str,
        reasons: list[str],
        status: str,
        evaluation_run_id: str | None = None,
        now: datetime | None = None,
    ) -> GovernanceAuditEventRow:
        now = now or datetime.now(UTC)
        outcome = DecisionOutcome.APPROVED if breach and to_state else DecisionOutcome.NO_ACTION
        if status in ("already_demoted", "skipped_insufficient_sample"):
            outcome = DecisionOutcome.DEFERRED

        criteria: list[GovernanceCriteriaEvaluation] = []
        if breached_metric is not None:
            criteria.append(
                GovernanceCriteriaEvaluation(
                    criterion=breached_metric,
                    threshold=threshold,
                    actual=breached_value,
                    passed=not breach,
                    evaluated_at=now,
                )
            )

        rationale = _build_demotion_rationale(
            from_state=from_state,
            to_state=to_state,
            breach=breach,
            breached_metric=breached_metric,
            breached_value=breached_value,
            threshold=threshold,
            reasons=reasons,
            status=status,
        )
        evidence = GovernanceDecisionEvidence(
            governance_state_before=from_state,
            governance_state_after=to_state if breach else from_state,
            criteria_evaluated=criteria,
            metrics_snapshot={
                "source_id": source_id,
                "source_type": source_type,
                "breached_metric": breached_metric,
                "breached_value": breached_value,
                "threshold": threshold,
                "status": status,
            },
            source_run_id=source_id if source_type == "metrics_summary" else None,
            governance_policy_version=GOVERNANCE_POLICY_VERSION,
            evaluation_version=EVALUATION_VERSION,
        )
        row = self._build_and_record(
            strategy_id=strategy_id,
            event_type=(
                "GOVERNANCE_DEMOTION_EXECUTED"
                if (breach and to_state)
                else "GOVERNANCE_DEMOTION_SKIPPED"
            ),
            trigger_source=trigger_source,
            actor=actor,
            decision_outcome=outcome,
            decision_rationale=rationale,
            evidence=evidence,
            transition_from=from_state,
            transition_to=to_state,
            evaluation_run_id=evaluation_run_id,
            now=now,
        )

        _labels = {"trigger_source": trigger_source, "strategy_id": strategy_id}
        governance_decisions_total.add(1, _labels)
        governance_demotions_total.add(1, _labels)
        if trigger_source in (TriggerSource.AUTO_DEMOTION, TriggerSource.SYSTEM_RISK):
            governance_auto_actions_total.add(1, _labels)

        logger.info(
            "DEMOTION_EVIDENCE_RECORDED",
            extra={
                "governance_audit_id": row.governance_audit_id,
                "strategy_id": strategy_id,
                "from_state": from_state,
                "to_state": to_state,
                "breach": breach,
                "breached_metric": breached_metric,
                "trigger_source": trigger_source,
                "status": status,
            },
        )
        self._emit_observability_event(
            "GOVERNANCE_DECISION_RECORDED",
            strategy_id=strategy_id,
            transition=f"{from_state}->{to_state}",
            trigger_source=trigger_source,
            source_run_id=source_id,
            criteria_count=len(criteria),
            decision_outcome=str(outcome),
            occurred_at=now,
        )
        self._emit_observability_event(
            "DEMOTION_EVIDENCE_RECORDED",
            strategy_id=strategy_id,
            transition=f"{from_state}->{to_state}",
            trigger_source=trigger_source,
            source_run_id=source_id,
            criteria_count=len(criteria),
            decision_outcome=str(outcome),
            occurred_at=now,
        )
        return row

    def record_health_transition(
        self,
        *,
        strategy_id: str,
        from_status: str | None,
        to_status: str,
        actor: str,
        trigger_source: TriggerSource,
        transition_reason: str,
        eval_metrics: dict[str, Any] | None,
        allocation_penalty: float,
        allocation_scalar: float,
        did_transition: bool,
        governance_state: str | None = None,
        evaluation_run_id: str | None = None,
        now: datetime | None = None,
    ) -> GovernanceAuditEventRow:
        now = now or datetime.now(UTC)
        outcome = DecisionOutcome.APPROVED if did_transition else DecisionOutcome.NO_ACTION
        rationale = _build_health_rationale(
            from_status=from_status,
            to_status=to_status,
            reason=transition_reason,
            eval_metrics=eval_metrics,
            did_transition=did_transition,
        )
        evidence = GovernanceDecisionEvidence(
            governance_state_before=governance_state,
            governance_state_after=governance_state,
            health_status_before=from_status,
            health_status_after=to_status,
            criteria_evaluated=[],
            metrics_snapshot=eval_metrics or {},
            allocation_scalar=allocation_scalar,
            governance_policy_version=GOVERNANCE_POLICY_VERSION,
            evaluation_version=EVALUATION_VERSION,
        )
        row = self._build_and_record(
            strategy_id=strategy_id,
            event_type=(
                "GOVERNANCE_HEALTH_TRANSITION" if did_transition else "GOVERNANCE_HEALTH_NO_CHANGE"
            ),
            trigger_source=trigger_source,
            actor=actor,
            decision_outcome=outcome,
            decision_rationale=rationale,
            evidence=evidence,
            transition_from=from_status,
            transition_to=to_status,
            evaluation_run_id=evaluation_run_id,
            now=now,
        )

        governance_decisions_total.add(
            1, {"trigger_source": trigger_source, "strategy_id": strategy_id}
        )
        logger.info(
            "governance_audit.health_transition_recorded",
            extra={
                "governance_audit_id": row.governance_audit_id,
                "strategy_id": strategy_id,
                "from_status": from_status,
                "to_status": to_status,
                "did_transition": did_transition,
                "trigger_source": trigger_source,
            },
        )
        return row

    def record_drawdown_transition(
        self,
        *,
        strategy_id: str,
        from_state: str | None,
        to_state: str,
        actor: str,
        trigger_source: TriggerSource,
        transition_reason: str,
        realized_drawdown: float | None,
        max_drawdown_allowed: float | None,
        drawdown_utilization: float | None,
        allocation_scalar_after: float | None,
        evaluation_run_id: str | None = None,
        now: datetime | None = None,
    ) -> GovernanceAuditEventRow:
        now = now or datetime.now(UTC)
        rationale = _build_drawdown_rationale(
            from_state=from_state,
            to_state=to_state,
            realized_drawdown=realized_drawdown,
            max_drawdown_allowed=max_drawdown_allowed,
            drawdown_utilization=drawdown_utilization,
            allocation_scalar_after=allocation_scalar_after,
        )
        evidence = GovernanceDecisionEvidence(
            governance_state_before=None,
            governance_state_after=None,
            drawdown_utilization=drawdown_utilization,
            ladder_state=to_state,
            allocation_scalar=allocation_scalar_after,
            criteria_evaluated=[
                GovernanceCriteriaEvaluation(
                    criterion="drawdown_utilization",
                    threshold=None,
                    actual=drawdown_utilization,
                    passed=(to_state == "NORMAL"),
                    evaluated_at=now,
                )
            ]
            if drawdown_utilization is not None
            else [],
            metrics_snapshot={
                "realized_drawdown": realized_drawdown,
                "max_drawdown_allowed": max_drawdown_allowed,
                "drawdown_utilization": drawdown_utilization,
            },
            governance_policy_version=GOVERNANCE_POLICY_VERSION,
            evaluation_version=EVALUATION_VERSION,
        )
        row = self._build_and_record(
            strategy_id=strategy_id,
            event_type="GOVERNANCE_DRAWDOWN_LADDER_TRANSITION",
            trigger_source=trigger_source,
            actor=actor,
            decision_outcome=DecisionOutcome.ESCALATED
            if _is_drawdown_escalation(from_state, to_state)
            else DecisionOutcome.APPROVED,
            decision_rationale=rationale,
            evidence=evidence,
            transition_from=from_state,
            transition_to=to_state,
            evaluation_run_id=evaluation_run_id,
            now=now,
        )

        _labels = {"trigger_source": trigger_source, "strategy_id": strategy_id}
        governance_decisions_total.add(1, _labels)
        governance_auto_actions_total.add(1, _labels)

        logger.info(
            "governance_audit.drawdown_transition_recorded",
            extra={
                "governance_audit_id": row.governance_audit_id,
                "strategy_id": strategy_id,
                "from_state": from_state,
                "to_state": to_state,
                "drawdown_utilization": drawdown_utilization,
                "trigger_source": trigger_source,
            },
        )
        return row

    def supersede(
        self,
        *,
        governance_audit_id: str,
        superseded_by: str,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> GovernanceAuditEventRow | None:
        now = now or datetime.now(UTC)
        row = self._repo.mark_superseded(governance_audit_id, superseded_by=superseded_by)
        if row is None:
            logger.warning(
                "governance_audit.supersede_not_found",
                extra={"governance_audit_id": governance_audit_id, "actor": actor},
            )
            return None

        governance_superseded_events_total.add(1, {"actor": actor})
        logger.info(
            "GOVERNANCE_AUDIT_SUPERSEDED",
            extra={
                "governance_audit_id": governance_audit_id,
                "superseded_by": superseded_by,
                "actor": actor,
                "reason": reason,
            },
        )
        self._emit_observability_event(
            "GOVERNANCE_AUDIT_SUPERSEDED",
            strategy_id=row.strategy_id,
            transition=f"{row.transition_from}->{row.transition_to}",
            trigger_source=row.trigger_source,
            source_run_id=row.source_run_id,
            criteria_count=len(row.criteria_evaluated or []),
            decision_outcome=DecisionOutcome.SUPERSEDED,
            occurred_at=now,
            metadata={
                "governance_audit_id": governance_audit_id,
                "superseded_by": superseded_by,
                "reason": reason,
            },
        )
        return row

    def record_missing_evidence(
        self,
        *,
        strategy_id: str,
        transition: str,
        trigger_source: TriggerSource,
        reason: str,
        source_run_id: str | None = None,
        criteria_count: int = 0,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(UTC)
        self._emit_observability_event(
            "MISSING_GOVERNANCE_EVIDENCE",
            strategy_id=strategy_id,
            transition=transition,
            trigger_source=trigger_source,
            source_run_id=source_run_id,
            criteria_count=criteria_count,
            decision_outcome=DecisionOutcome.FAILED,
            occurred_at=now,
            metadata={"reason": reason},
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_decision(self, governance_audit_id: str) -> GovernanceAuditRecord | None:
        row = self._repo.get_by_id(governance_audit_id)
        if row is None:
            return None
        return GovernanceAuditRepository.row_to_record(row)

    def list_decisions(
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
    ) -> GovernanceAuditListResult:
        rows, total = self._repo.list_events(
            page=page,
            page_size=page_size,
            strategy_id=strategy_id,
            trigger_source=trigger_source,
            event_type=event_type,
            decision_outcome=decision_outcome,
            from_date=from_date,
            to_date=to_date,
        )
        return GovernanceAuditListResult(
            records=[GovernanceAuditRepository.row_to_record(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_supersession_chain(self, governance_audit_id: str) -> list[GovernanceAuditRecord]:
        rows = self._repo.get_supersession_chain(governance_audit_id)
        return [GovernanceAuditRepository.row_to_record(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_and_record(
        self,
        *,
        strategy_id: str,
        event_type: str,
        trigger_source: TriggerSource,
        actor: str,
        decision_outcome: DecisionOutcome,
        decision_rationale: str,
        evidence: GovernanceDecisionEvidence,
        transition_from: str | None = None,
        transition_to: str | None = None,
        evaluation_run_id: str | None = None,
        now: datetime,
    ) -> GovernanceAuditEventRow:
        ev = evidence.to_jsonable()
        row = GovernanceAuditRepository._build_row(
            governance_audit_id=str(uuid4()),
            strategy_id=strategy_id,
            event_type=event_type,
            trigger_source=str(trigger_source),
            actor=actor,
            decision_outcome=str(decision_outcome),
            recorded_at=now,
            transition_from=transition_from or ev.get("governance_state_before"),
            transition_to=transition_to or ev.get("governance_state_after"),
            decision_rationale=decision_rationale,
            criteria_evaluated=[c.to_jsonable() for c in evidence.criteria_evaluated],
            metrics_lineage={
                "source_run_id": evidence.source_run_id,
                "simulation_run_id": evidence.simulation_run_id,
                "experiment_run_id": evidence.experiment_run_id,
                "allocation_config_hash": evidence.allocation_config_hash,
                "covariance_snapshot_id": evidence.covariance_snapshot_id,
                "factor_snapshot_id": evidence.factor_snapshot_id,
            },
            state_snapshot_before={
                "governance_state": ev.get("governance_state_before"),
                "health_status": ev.get("health_status_before"),
                "allocation_status": ev.get("allocation_status"),
                "ladder_state": ev.get("ladder_state"),
                "metrics": evidence.metrics_snapshot,
            },
            state_snapshot_after={
                "governance_state": ev.get("governance_state_after"),
                "health_status": ev.get("health_status_after"),
                "allocation_scalar": ev.get("allocation_scalar"),
                "drawdown_utilization": ev.get("drawdown_utilization"),
            },
            source_run_id=evidence.source_run_id,
            simulation_run_id=evidence.simulation_run_id,
            experiment_run_id=evidence.experiment_run_id,
            allocation_config_hash=evidence.allocation_config_hash,
            covariance_snapshot_id=evidence.covariance_snapshot_id,
            factor_snapshot_id=evidence.factor_snapshot_id,
            promotion_rule_version=evidence.promotion_rule_version,
            governance_policy_version=evidence.governance_policy_version,
            evaluation_version=evidence.evaluation_version,
            shadow_validation_status=evidence.shadow_validation_status,
            shadow_divergence_metrics=evidence.shadow_divergence_metrics,
            evaluation_run_id=evaluation_run_id,
        )
        self._repo.record(row)
        return row

    def _emit_observability_event(
        self,
        event_type: str,
        *,
        strategy_id: str,
        transition: str | None,
        trigger_source: TriggerSource | str,
        source_run_id: str | None,
        criteria_count: int,
        decision_outcome: DecisionOutcome | str,
        occurred_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "strategy_id": strategy_id,
            "transition": transition,
            "trigger_source": str(trigger_source),
            "source_run_id": source_run_id,
            "criteria_count": criteria_count,
            "decision_outcome": str(decision_outcome),
            **(metadata or {}),
        }
        self._audit_log_repo.record_operator_action(
            action=event_type,
            actor=str(trigger_source),
            reason=event_type.lower(),
            occurred_at=occurred_at,
            component="governance",
            metadata=payload,
        )


# ---------------------------------------------------------------------------
# Pure rationale builders (no I/O, fully testable)
# ---------------------------------------------------------------------------


def _build_promotion_rationale(
    *,
    from_state: str,
    to_state: str,
    criteria: list[GovernanceCriteriaEvaluation],
    eligible: bool,
    reasons: list[str],
) -> str:
    lines = [f"Promotion evaluation: {from_state} -> {to_state}"]
    for c in criteria:
        if c.threshold is None:
            continue
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  {mark} {c.criterion}: actual={c.actual}, threshold={c.threshold}")
    if eligible:
        lines.append("Decision: APPROVED - all required criteria passed.")
    else:
        lines.append("Decision: REJECTED")
        for r in reasons:
            lines.append(f"  - {r}")
    return "\n".join(lines)


def _requires_source_run(*, to_state: str, trigger_source: TriggerSource) -> bool:
    return trigger_source == TriggerSource.AUTO_PROMOTION or to_state in {
        "approved_for_paper_trading",
        "approved_paper",
        "paper",
        "approved_for_live_trading",
        "approved_live",
        "live",
    }


def _build_demotion_rationale(
    *,
    from_state: str,
    to_state: str | None,
    breach: bool,
    breached_metric: str | None,
    breached_value: float | None,
    threshold: float | None,
    reasons: list[str],
    status: str,
) -> str:
    if not breach:
        return f"No demotion required. Status: {status}. Reasons: {'; '.join(reasons)}"
    lines = [f"Demotion evaluation: {from_state} -> {to_state or 'unknown'}"]
    if breached_metric:
        lines.append(
            f"  Breached: {breached_metric}={breached_value} exceeds threshold={threshold}"
        )
    for r in reasons:
        lines.append(f"  - {r}")
    lines.append(f"Decision: DEMOTION EXECUTED (status={status})")
    return "\n".join(lines)


def _build_health_rationale(
    *,
    from_status: str | None,
    to_status: str,
    reason: str,
    eval_metrics: dict[str, Any] | None,
    did_transition: bool,
) -> str:
    if not did_transition:
        return f"Health evaluation: no status change. Current={from_status}. Reason: {reason}"
    lines = [f"Health lifecycle: {from_status} -> {to_status}"]
    lines.append(f"  Trigger: {reason}")
    if eval_metrics:
        for k, v in eval_metrics.items():
            if v is not None:
                lines.append(f"  {k}: {v}")
    lines.append(f"Decision: TRANSITION APPLIED ({from_status} -> {to_status})")
    return "\n".join(lines)


def _build_drawdown_rationale(
    *,
    from_state: str | None,
    to_state: str,
    realized_drawdown: float | None,
    max_drawdown_allowed: float | None,
    drawdown_utilization: float | None,
    allocation_scalar_after: float | None,
) -> str:
    lines = [f"Drawdown governance: {from_state} -> {to_state}"]
    if realized_drawdown is not None:
        lines.append(f"  realized_drawdown: {realized_drawdown:.4f}")
    if max_drawdown_allowed is not None:
        lines.append(f"  max_drawdown_allowed: {max_drawdown_allowed:.4f}")
    if drawdown_utilization is not None:
        lines.append(f"  drawdown_utilization: {drawdown_utilization:.2%}")
    if allocation_scalar_after is not None:
        lines.append(f"  allocation_scalar_after: {allocation_scalar_after:.2f}")
    lines.append(f"Decision: LADDER TRANSITION TO {to_state}")
    return "\n".join(lines)


_DRAWDOWN_SEVERITY = {"NORMAL": 0, "WARNING": 1, "PROBATION": 2, "SUSPENDED": 3, "BREACHED": 4}


def _is_drawdown_escalation(from_state: str | None, to_state: str) -> bool:
    if from_state is None:
        return True
    return _DRAWDOWN_SEVERITY.get(to_state, 0) > _DRAWDOWN_SEVERITY.get(from_state, 0)

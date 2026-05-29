from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.contracts.governance.governance_audit import (
    GovernanceCriteriaEvaluation,
    TriggerSource,
)
from autonomous_trading_platform.storage.sor.models.audit_logs import AuditLogRow
from autonomous_trading_platform.storage.sor.models.governance_audit_events import (
    GovernanceAuditEventRow,
)


def test_promotion_decision_persists_complete_machine_readable_evidence(
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    row = GovernanceAuditService(session=db_session).record_promotion_decision(
        strategy_id="strategy-a",
        from_state="approved_research",
        to_state="approved_for_paper_trading",
        actor="operator-1",
        trigger_source=TriggerSource.OPERATOR_MANUAL,
        eligible=True,
        criteria=[
            GovernanceCriteriaEvaluation(
                criterion="min_sharpe",
                threshold=1.5,
                actual=1.82,
                passed=True,
                evaluated_at=now,
            ),
            GovernanceCriteriaEvaluation(
                criterion="max_drawdown",
                threshold=0.10,
                actual=0.07,
                passed=True,
                evaluated_at=now,
            ),
        ],
        metrics_snapshot={"sharpe": 1.82, "max_drawdown": 0.07},
        source_run_id="sim-run-1",
        rule_id="rule-v1",
        reasons=["operator reviewed simulation evidence"],
        evaluation_run_id="review-run-1",
        now=now,
    )
    db_session.flush()

    stored = db_session.get(GovernanceAuditEventRow, row.governance_audit_id)
    assert stored is not None
    assert stored.trigger_source == "operator_manual"
    assert stored.source_run_id == "sim-run-1"
    assert stored.promotion_rule_version == "rule-v1"
    assert stored.criteria_evaluated[0]["criterion"] == "min_sharpe"
    assert stored.criteria_evaluated[0]["threshold"] == 1.5
    assert stored.metrics_lineage["source_run_id"] == "sim-run-1"
    assert stored.state_snapshot_before["governance_state"] == "approved_research"
    assert stored.state_snapshot_after["governance_state"] == "approved_for_paper_trading"
    assert "Decision: APPROVED" in stored.decision_rationale

    observability = (
        db_session.query(AuditLogRow).filter_by(event_type="GOVERNANCE_DECISION_RECORDED").one()
    )
    assert observability.event_metadata["strategy_id"] == "strategy-a"
    assert observability.event_metadata["criteria_count"] == 2


def test_supersession_chain_preserves_amendment_link(db_session: Session) -> None:
    service = GovernanceAuditService(session=db_session)
    first = service.record_demotion_decision(
        strategy_id="strategy-b",
        from_state="approved_for_live_trading",
        to_state="approved_for_paper_trading",
        actor="system_risk",
        trigger_source=TriggerSource.AUTO_DEMOTION,
        breach=True,
        breached_metric="drawdown",
        breached_value=0.25,
        threshold=0.12,
        source_id="metrics-1",
        source_type="metrics_summary",
        reasons=["drawdown breached threshold"],
        status="demoted",
    )
    amendment = service.record_demotion_decision(
        strategy_id="strategy-b",
        from_state="approved_for_paper_trading",
        to_state="approved_research",
        actor="operator-1",
        trigger_source=TriggerSource.OPERATOR_MANUAL,
        breach=True,
        breached_metric="drawdown",
        breached_value=0.25,
        threshold=0.12,
        source_id="metrics-1",
        source_type="amendment",
        reasons=["corrected demotion target"],
        status="manual_amendment",
    )
    service.supersede(
        governance_audit_id=first.governance_audit_id,
        superseded_by=amendment.governance_audit_id,
        actor="operator-1",
        reason="corrected target state",
    )
    db_session.flush()

    chain = service.get_supersession_chain(first.governance_audit_id)
    assert [row.governance_audit_id for row in chain] == [
        first.governance_audit_id,
        amendment.governance_audit_id,
    ]
    assert chain[0].superseded_by == amendment.governance_audit_id
    assert (
        db_session.query(AuditLogRow).filter_by(event_type="GOVERNANCE_AUDIT_SUPERSEDED").count()
        == 1
    )

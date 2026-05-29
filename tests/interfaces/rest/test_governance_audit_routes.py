from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.governance_audit_events import (
    GovernanceAuditEventRow,
)
from tests.conftest import auth_headers


def test_governance_audit_route_returns_decision_evidence(
    client: TestClient,
    db_session: Session,
) -> None:
    db_session.add(
        GovernanceAuditEventRow(
            governance_audit_id="audit-1",
            strategy_id="strategy-a",
            event_type="GOVERNANCE_PROMOTION_APPROVED",
            trigger_source="operator_manual",
            transition_from="approved_research",
            transition_to="approved_for_paper_trading",
            actor="operator-1",
            decision_outcome="approved",
            decision_rationale="Promotion approved from simulation evidence.",
            criteria_evaluated=[
                {
                    "criterion": "min_sharpe",
                    "threshold": 1.5,
                    "actual": 1.82,
                    "passed": True,
                    "evaluated_at": datetime.now(UTC).isoformat(),
                }
            ],
            source_run_id="sim-run-1",
            simulation_run_id="sim-run-1",
            experiment_run_id="experiment-1",
            allocation_config_hash="alloc-hash",
            covariance_snapshot_id=None,
            factor_snapshot_id=None,
            metrics_lineage={"source_run_id": "sim-run-1"},
            state_snapshot_before={"governance_state": "approved_research"},
            state_snapshot_after={"governance_state": "approved_for_paper_trading"},
            promotion_rule_version="rule-v1",
            governance_policy_version="1.0.0",
            evaluation_version="1.0.0",
            shadow_validation_status=None,
            shadow_divergence_metrics=None,
            superseded_by=None,
            evaluation_run_id="review-run-1",
            recorded_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    response = client.get(
        "/api/v1/governance-audit/audit-1",
        headers=auth_headers(role="operator"),
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["governance_audit_id"] == "audit-1"
    assert payload["trigger_source"] == "operator_manual"
    assert payload["source_run_id"] == "sim-run-1"
    assert payload["criteria_evaluated"][0]["criterion"] == "min_sharpe"
    assert payload["metrics_lineage"]["source_run_id"] == "sim-run-1"


def test_governance_audit_route_filters_by_trigger_source(
    client: TestClient,
    db_session: Session,
) -> None:
    now = datetime.now(UTC)
    db_session.add_all(
        [
            _row("audit-auto", "auto_promotion", now),
            _row("audit-manual", "operator_manual", now),
        ]
    )
    db_session.flush()

    response = client.get(
        "/api/v1/governance-audit",
        params={"trigger_source": "auto_promotion"},
        headers=auth_headers(role="admin"),
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["records"][0]["governance_audit_id"] == "audit-auto"


def _row(audit_id: str, trigger_source: str, recorded_at: datetime) -> GovernanceAuditEventRow:
    return GovernanceAuditEventRow(
        governance_audit_id=audit_id,
        strategy_id="strategy-a",
        event_type="GOVERNANCE_PROMOTION_APPROVED",
        trigger_source=trigger_source,
        transition_from="approved_research",
        transition_to="approved_for_paper_trading",
        actor="test",
        decision_outcome="approved",
        decision_rationale="approved",
        criteria_evaluated=[],
        source_run_id="sim-run-1",
        simulation_run_id=None,
        experiment_run_id=None,
        allocation_config_hash=None,
        covariance_snapshot_id=None,
        factor_snapshot_id=None,
        metrics_lineage={"source_run_id": "sim-run-1"},
        state_snapshot_before={},
        state_snapshot_after={},
        promotion_rule_version=None,
        governance_policy_version="1.0.0",
        evaluation_version="1.0.0",
        shadow_validation_status=None,
        shadow_divergence_metrics=None,
        superseded_by=None,
        evaluation_run_id=None,
        recorded_at=recorded_at,
    )

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.governance_audit_service import (
    GovernanceAuditService,
)
from autonomous_trading_platform.contracts.governance.governance_audit import GovernanceAuditRecord
from autonomous_trading_platform.db import get_session

router = APIRouter(prefix="/governance-audit", tags=["governance-audit"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class GovernanceAuditEventResponse(BaseModel):
    governance_audit_id: str
    strategy_id: str
    event_type: str
    trigger_source: str
    transition_from: str | None
    transition_to: str | None
    actor: str
    decision_outcome: str
    decision_rationale: str
    criteria_evaluated: list[dict[str, Any]]
    metrics_lineage: dict[str, Any]
    state_snapshot_before: dict[str, Any]
    state_snapshot_after: dict[str, Any]
    source_run_id: str | None
    simulation_run_id: str | None
    experiment_run_id: str | None
    allocation_config_hash: str | None
    covariance_snapshot_id: str | None
    factor_snapshot_id: str | None
    promotion_rule_version: str | None
    governance_policy_version: str | None
    evaluation_version: str | None
    shadow_validation_status: str | None
    shadow_divergence_metrics: dict[str, Any] | None
    superseded_by: str | None
    evaluation_run_id: str | None
    recorded_at: datetime


class GovernanceAuditListResponse(BaseModel):
    records: list[GovernanceAuditEventResponse]
    total: int
    page: int
    page_size: int


class SupersedeRequest(BaseModel):
    superseded_by: str
    reason: str


def _record_to_response(record: GovernanceAuditRecord) -> GovernanceAuditEventResponse:
    return GovernanceAuditEventResponse(
        governance_audit_id=record.governance_audit_id,
        strategy_id=record.strategy_id,
        event_type=record.event_type,
        trigger_source=record.trigger_source,
        transition_from=record.transition_from,
        transition_to=record.transition_to,
        actor=record.actor,
        decision_outcome=record.decision_outcome,
        decision_rationale=record.decision_rationale,
        criteria_evaluated=record.criteria_evaluated,
        metrics_lineage=record.metrics_lineage,
        state_snapshot_before=record.state_snapshot_before,
        state_snapshot_after=record.state_snapshot_after,
        source_run_id=record.source_run_id,
        simulation_run_id=record.simulation_run_id,
        experiment_run_id=record.experiment_run_id,
        allocation_config_hash=record.allocation_config_hash,
        covariance_snapshot_id=record.covariance_snapshot_id,
        factor_snapshot_id=record.factor_snapshot_id,
        promotion_rule_version=record.promotion_rule_version,
        governance_policy_version=record.governance_policy_version,
        evaluation_version=record.evaluation_version,
        shadow_validation_status=record.shadow_validation_status,
        shadow_divergence_metrics=record.shadow_divergence_metrics,
        superseded_by=record.superseded_by,
        evaluation_run_id=record.evaluation_run_id,
        recorded_at=record.recorded_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessEnvelope[GovernanceAuditListResponse],
    summary="List governance audit decisions",
    description=(
        "Query the governance audit log with optional filters. "
        "Returns machine-readable decision evidence for every governance action."
    ),
)
def list_governance_audit_decisions(
    request_id: Annotated[str, _request_id_dependency],
    session: Annotated[Session, _session_dependency],
    _auth=Depends(require_operator_or_admin),
    strategy_id: str | None = Query(None),
    trigger_source: str | None = Query(None),
    event_type: str | None = Query(None),
    decision_outcome: str | None = Query(None),
    from_date: datetime | None = Query(None),
    to_date: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> SuccessEnvelope[GovernanceAuditListResponse]:
    service = GovernanceAuditService(session=session)
    result = service.list_decisions(
        page=page,
        page_size=page_size,
        strategy_id=strategy_id,
        trigger_source=trigger_source,
        event_type=event_type,
        decision_outcome=decision_outcome,
        from_date=from_date,
        to_date=to_date,
    )
    return success_response(
        GovernanceAuditListResponse(
            records=[_record_to_response(r) for r in result.records],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        ),
        request_id=request_id,
    )


@router.get(
    "/strategy/{strategy_id}",
    response_model=SuccessEnvelope[GovernanceAuditListResponse],
    summary="All governance decisions for a strategy",
)
def list_strategy_governance_decisions(
    strategy_id: str,
    request_id: Annotated[str, _request_id_dependency],
    session: Annotated[Session, _session_dependency],
    _auth=Depends(require_operator_or_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> SuccessEnvelope[GovernanceAuditListResponse]:
    service = GovernanceAuditService(session=session)
    result = service.list_decisions(
        page=page,
        page_size=page_size,
        strategy_id=strategy_id,
    )
    return success_response(
        GovernanceAuditListResponse(
            records=[_record_to_response(r) for r in result.records],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
        ),
        request_id=request_id,
    )


@router.get(
    "/{governance_audit_id}",
    response_model=SuccessEnvelope[GovernanceAuditEventResponse],
    summary="Get a single governance audit decision with full evidence",
)
def get_governance_audit_decision(
    governance_audit_id: str,
    request_id: Annotated[str, _request_id_dependency],
    session: Annotated[Session, _session_dependency],
    _auth=Depends(require_operator_or_admin),
) -> SuccessEnvelope[GovernanceAuditEventResponse]:
    service = GovernanceAuditService(session=session)
    record = service.get_decision(governance_audit_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Governance audit event {governance_audit_id!r} not found.",
        )
    return success_response(_record_to_response(record), request_id=request_id)


@router.get(
    "/{governance_audit_id}/supersession-chain",
    response_model=SuccessEnvelope[list[GovernanceAuditEventResponse]],
    summary="Trace the supersession chain for a governance audit event",
    description=(
        "Follow the superseded_by chain from the given audit event back to the root. "
        "Returns the full list of events in order from most recent to oldest."
    ),
)
def get_supersession_chain(
    governance_audit_id: str,
    request_id: Annotated[str, _request_id_dependency],
    session: Annotated[Session, _session_dependency],
    _auth=Depends(require_operator_or_admin),
) -> SuccessEnvelope[list[GovernanceAuditEventResponse]]:
    service = GovernanceAuditService(session=session)
    chain = service.get_supersession_chain(governance_audit_id)
    return success_response(
        [_record_to_response(r) for r in chain],
        request_id=request_id,
    )


@router.post(
    "/{governance_audit_id}/supersede",
    response_model=SuccessEnvelope[GovernanceAuditEventResponse],
    summary="Mark a governance audit event as superseded by an amendment",
    description=(
        "Governance amendments are expressed by creating a new audit event and then "
        "marking the original event as superseded. This preserves immutable audit history "
        "while making amendment chains fully traceable."
    ),
)
def supersede_governance_audit_event(
    governance_audit_id: str,
    body: SupersedeRequest,
    request_id: Annotated[str, _request_id_dependency],
    session: Annotated[Session, _session_dependency],
    auth=Depends(require_operator_or_admin),
) -> SuccessEnvelope[GovernanceAuditEventResponse]:
    actor = getattr(auth, "sub", "operator")
    service = GovernanceAuditService(session=session)
    row = service.supersede(
        governance_audit_id=governance_audit_id,
        superseded_by=body.superseded_by,
        actor=actor,
        reason=body.reason,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Governance audit event {governance_audit_id!r} not found.",
        )
    from autonomous_trading_platform.storage.sor.repositories.core.governance_audit_repository import (
        GovernanceAuditRepository,
    )

    session.flush()
    session.commit()
    record = GovernanceAuditRepository.row_to_record(row)
    return success_response(_record_to_response(record), request_id=request_id)

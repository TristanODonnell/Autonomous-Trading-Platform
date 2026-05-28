from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
    require_risk_manager_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.drawdown_governance_service import (
    DrawdownGovernanceService,
)
from autonomous_trading_platform.contracts.governance.drawdown_governance import (
    DrawdownGovernanceLadderConfigResponse,
    DrawdownGovernanceLadderSummary,
    DrawdownGovernanceLadderTransitionRecord,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_state_repository import (
    DrawdownGovernanceLadderStateRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.drawdown_governance_ladder_transition_repository import (
    DrawdownGovernanceLadderTransitionRepository,
)

router = APIRouter(prefix="/drawdown-governance", tags=["drawdown-governance"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


# ---------------------------------------------------------------------------
# Response schema helpers
# ---------------------------------------------------------------------------


def _state_row_to_summary(row) -> DrawdownGovernanceLadderSummary:
    return DrawdownGovernanceLadderSummary(
        strategy_id=row.strategy_id,
        ladder_state=row.ladder_state,
        drawdown_utilization=row.drawdown_utilization,
        realized_drawdown=row.realized_drawdown,
        max_drawdown_allowed=row.max_drawdown_allowed,
        allocation_scalar=row.allocation_scalar,
        suggested_health_status=_suggest_health(row.ladder_state),
        operator_ack_required=row.operator_ack_required,
        cooldown_expires_at=row.cooldown_expires_at,
        last_transition_at=row.last_transition_at,
        last_evaluated_at=row.last_evaluated_at,
        evaluation_count=row.evaluation_count or 0,
    )


def _suggest_health(state: str) -> str:
    from autonomous_trading_platform.contracts.governance.drawdown_governance import (
        LADDER_HEALTH_SUGGESTIONS,
        DrawdownGovernanceState,
    )

    return LADDER_HEALTH_SUGGESTIONS.get(
        state, LADDER_HEALTH_SUGGESTIONS[DrawdownGovernanceState.NORMAL]
    )


def _transition_row_to_record(row) -> DrawdownGovernanceLadderTransitionRecord:
    return DrawdownGovernanceLadderTransitionRecord(
        transition_id=row.transition_id,
        strategy_id=row.strategy_id,
        from_state=row.from_state,
        to_state=row.to_state,
        transition_reason=row.transition_reason or "",
        triggered_by=row.triggered_by,
        drawdown_utilization=row.drawdown_utilization,
        allocation_scalar_after=row.allocation_scalar_after,
        cooldown_expires_at=row.cooldown_expires_at,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# List endpoints
# ---------------------------------------------------------------------------


class DrawdownGovernanceLadderListResponse(BaseModel):
    strategies: list[DrawdownGovernanceLadderSummary]
    total: int


class DrawdownGovernanceLadderTransitionListResponse(BaseModel):
    transitions: list[DrawdownGovernanceLadderTransitionRecord]
    total: int


class DrawdownGovernanceAckRequest(BaseModel):
    operator: str
    reason: str


class DrawdownGovernanceAckResponse(BaseModel):
    strategy_id: str
    acknowledged: bool


class DrawdownGovernanceTriggerResponse(BaseModel):
    run_id: str
    strategies_evaluated: int
    transitions: int
    warnings_triggered: int
    probations_triggered: int
    suspensions_triggered: int
    breaches_triggered: int
    recoveries_triggered: int
    duration_seconds: float


@router.get(
    "",
    response_model=SuccessEnvelope[DrawdownGovernanceLadderListResponse],
)
def list_drawdown_governance_states(
    state: Annotated[str | None, Query(description="Filter by ladder_state")] = None,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceLadderListResponse]:
    repo = DrawdownGovernanceLadderStateRepository(session)
    rows = repo.get_by_state(state) if state else repo.get_all()
    summaries = [_state_row_to_summary(r) for r in rows]
    return success_response(
        data=DrawdownGovernanceLadderListResponse(strategies=summaries, total=len(summaries)),
        request_id=request_id,
    )


@router.get(
    "/pending-ack",
    response_model=SuccessEnvelope[DrawdownGovernanceLadderListResponse],
)
def list_pending_operator_ack(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceLadderListResponse]:
    repo = DrawdownGovernanceLadderStateRepository(session)
    rows = repo.get_pending_operator_ack()
    summaries = [_state_row_to_summary(r) for r in rows]
    return success_response(
        data=DrawdownGovernanceLadderListResponse(strategies=summaries, total=len(summaries)),
        request_id=request_id,
    )


@router.get(
    "/config",
    response_model=SuccessEnvelope[DrawdownGovernanceLadderConfigResponse],
)
def get_ladder_config(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceLadderConfigResponse]:
    svc = DrawdownGovernanceService(session=session)
    cfg = svc._load_config()
    return success_response(
        data=DrawdownGovernanceLadderConfigResponse(
            enabled=cfg.enabled,
            mode=cfg.mode,
            warning_threshold=cfg.warning_threshold,
            probation_threshold=cfg.probation_threshold,
            suspension_threshold=cfg.suspension_threshold,
            breach_threshold=cfg.breach_threshold,
            normal_allocation_scalar=cfg.normal_allocation_scalar,
            warning_allocation_scalar=cfg.warning_allocation_scalar,
            probation_allocation_scalar=cfg.probation_allocation_scalar,
            suspended_allocation_scalar=cfg.suspended_allocation_scalar,
            breached_allocation_scalar=cfg.breached_allocation_scalar,
            hysteresis_band=cfg.hysteresis_band,
            breach_requires_operator_ack=cfg.breach_requires_operator_ack,
            auto_demote_on_breach=cfg.auto_demote_on_breach,
        ),
        request_id=request_id,
    )


@router.get(
    "/{strategy_id}",
    response_model=SuccessEnvelope[DrawdownGovernanceLadderSummary],
)
def get_strategy_ladder_state(
    strategy_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceLadderSummary]:
    repo = DrawdownGovernanceLadderStateRepository(session)
    row = repo.get_for_strategy(strategy_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No drawdown governance state found for strategy '{strategy_id}'",
        )
    return success_response(data=_state_row_to_summary(row), request_id=request_id)


@router.get(
    "/{strategy_id}/transitions",
    response_model=SuccessEnvelope[DrawdownGovernanceLadderTransitionListResponse],
)
def get_strategy_transitions(
    strategy_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceLadderTransitionListResponse]:
    repo = DrawdownGovernanceLadderTransitionRepository(session)
    rows = repo.get_recent_for_strategy(strategy_id, limit=limit)
    records = [_transition_row_to_record(r) for r in rows]
    return success_response(
        data=DrawdownGovernanceLadderTransitionListResponse(
            transitions=records, total=len(records)
        ),
        request_id=request_id,
    )


@router.post(
    "/{strategy_id}/acknowledge-breach",
    response_model=SuccessEnvelope[DrawdownGovernanceAckResponse],
    status_code=status.HTTP_200_OK,
)
def acknowledge_breach(
    strategy_id: str,
    payload: DrawdownGovernanceAckRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_operator_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceAckResponse]:
    svc = DrawdownGovernanceService(session=session)
    acknowledged = svc.acknowledge_breach(
        strategy_id,
        operator=payload.operator,
        reason=payload.reason,
    )
    if not acknowledged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Strategy '{strategy_id}' does not have a pending breach acknowledgement "
                "or no drawdown governance state exists."
            ),
        )
    return success_response(
        data=DrawdownGovernanceAckResponse(strategy_id=strategy_id, acknowledged=True),
        request_id=request_id,
    )


@router.post(
    "/run",
    response_model=SuccessEnvelope[DrawdownGovernanceTriggerResponse],
    status_code=status.HTTP_200_OK,
)
def trigger_ladder_evaluation(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _auth=Depends(require_operator_or_admin),
) -> SuccessEnvelope[DrawdownGovernanceTriggerResponse]:
    svc = DrawdownGovernanceService(session=session)
    result = svc.run()
    return success_response(
        data=DrawdownGovernanceTriggerResponse(
            run_id=result.run_id,
            strategies_evaluated=result.strategies_evaluated,
            transitions=len(result.transitions),
            warnings_triggered=result.warnings_triggered,
            probations_triggered=result.probations_triggered,
            suspensions_triggered=result.suspensions_triggered,
            breaches_triggered=result.breaches_triggered,
            recoveries_triggered=result.recoveries_triggered,
            duration_seconds=result.duration_seconds,
        ),
        request_id=request_id,
    )

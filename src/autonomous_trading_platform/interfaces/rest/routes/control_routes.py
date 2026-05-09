from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.control_schema import (
    ControlsStateResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    RuntimeControlActionRequest,
    RuntimeControlActionResponse,
    StrategyControlStateResponse,
)

router = APIRouter(prefix="/controls", tags=["controls"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "/state",
    response_model=SuccessEnvelope[ControlsStateResponse],
    status_code=status.HTTP_200_OK,
)
def get_controls_state(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ControlsStateResponse]:
    service = RuntimeControlService(session=session)
    result = service.get_controls_state()

    return success_response(
        data=ControlsStateResponse(
            kill_switch_active=result.kill_switch_active,
            trading_enabled=result.trading_enabled,
            trading_paused=result.trading_paused,
            trading_mode=result.trading_mode,
            reason=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
            strategies=[
                StrategyControlStateResponse(
                    strategy_id=strategy.strategy_id,
                    enabled=strategy.enabled,
                    status=strategy.status,
                    reason=strategy.reason,
                    updated_by=strategy.updated_by,
                    updated_at=strategy.updated_at,
                )
                for strategy in result.strategies
            ],
        ),
        request_id=request_id,
    )


@router.post(
    "/kill-switch",
    response_model=SuccessEnvelope[KillSwitchResponse],
    status_code=status.HTTP_200_OK,
)
def activate_kill_switch(
    payload: KillSwitchRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[KillSwitchResponse]:
    if not payload.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Kill switch reason is required.",
        )

    service = RuntimeControlService(session=session)
    result = service.activate_kill_switch(
        triggered_by=actor,
        reason=payload.reason.strip(),
    )

    return success_response(
        data=KillSwitchResponse(
            status="halted",
            kill_switch_active=result.kill_switch_active,
            canceled_order_count=result.canceled_order_count,
            reason=result.reason,
            triggered_by=result.triggered_by,
            triggered_at=result.triggered_at,
        ),
        request_id=request_id,
    )


@router.post(
    "/pause",
    response_model=SuccessEnvelope[RuntimeControlActionResponse],
    status_code=status.HTTP_200_OK,
)
def pause_trading(
    payload: RuntimeControlActionRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[RuntimeControlActionResponse]:
    rationale = payload.rationale.strip()
    if not rationale:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pause rationale is required.",
        )

    service = RuntimeControlService(session=session)
    result = service.pause_trading(updated_by=actor, rationale=rationale)

    return success_response(
        data=RuntimeControlActionResponse(
            status="paused",
            trading_paused=result.trading_paused,
            rationale=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )


@router.post(
    "/resume",
    response_model=SuccessEnvelope[RuntimeControlActionResponse],
    status_code=status.HTTP_200_OK,
)
def resume_trading(
    payload: RuntimeControlActionRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[RuntimeControlActionResponse]:
    rationale = payload.rationale.strip()
    if not rationale:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Resume rationale is required.",
        )

    service = RuntimeControlService(session=session)
    result = service.resume_trading(updated_by=actor, rationale=rationale)

    return success_response(
        data=RuntimeControlActionResponse(
            status="resumed",
            trading_paused=result.trading_paused,
            rationale=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )

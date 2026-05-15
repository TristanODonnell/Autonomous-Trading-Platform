from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_alpaca_broker_client,
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.health.detailed_system_health_service import (
    DetailedSystemHealthService,
)
from autonomous_trading_platform.application.services.runtime_control_service import (
    RuntimeControlService,
)
from autonomous_trading_platform.application.services.system_health_service import (
    SystemHealthService,
)
from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.contracts.runtime.detailed_health import (
    DetailedSystemHealthReport,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.system_schemas import (
    SystemHealthResponse,
    TradingModeUpdateRequest,
    TradingModeUpdateResponse,
)

router = APIRouter(prefix="/system", tags=["system"])
_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get("/health", response_model=SuccessEnvelope[SystemHealthResponse])
def get_system_health(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[SystemHealthResponse]:
    settings = Settings()
    service = SystemHealthService(session=session, settings=settings)
    result = service.get_health()

    return success_response(
        data=SystemHealthResponse(**result),
        request_id=request_id,
    )


@router.get(
    "/health/detailed",
    response_model=SuccessEnvelope[DetailedSystemHealthReport],
)
def get_detailed_system_health(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    broker_client=Depends(get_alpaca_broker_client),
) -> SuccessEnvelope[DetailedSystemHealthReport]:
    service = DetailedSystemHealthService(session=session, broker_client=broker_client)
    report = service.get_health()
    return success_response(data=report, request_id=request_id)


@router.put(
    "/trading-mode",
    response_model=SuccessEnvelope[TradingModeUpdateResponse],
    status_code=status.HTTP_200_OK,
)
def update_trading_mode(
    payload: TradingModeUpdateRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[TradingModeUpdateResponse]:
    service = RuntimeControlService(session=session)

    try:
        result = service.update_trading_mode(
            updated_by=actor,
            mode=payload.mode,
            rationale=payload.rationale.strip() if payload.rationale else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return success_response(
        data=TradingModeUpdateResponse(
            mode=result.mode,
            previous_mode=result.previous_mode,
            rationale=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )

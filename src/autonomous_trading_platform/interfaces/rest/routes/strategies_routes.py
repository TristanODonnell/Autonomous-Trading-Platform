from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
    require_risk_manager_or_admin,
)
from autonomous_trading_platform.api.envelope import (
    SuccessEnvelope,
    success_response,
)
from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.application.services.strategy_control_service import (
    StrategyControlService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.active_strategies_schema import (
    ActiveStrategiesResponse,
    StrategyAllocationUpdateRequest,
    StrategyAllocationUpdateResponse,
    StrategyEnabledUpdateRequest,
    StrategyEnabledUpdateResponse,
    StrategyGovernanceTransitionRequest,
    StrategyGovernanceTransitionResponse,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "/active",
    response_model=SuccessEnvelope[ActiveStrategiesResponse],
)
def get_active_strategies(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ActiveStrategiesResponse]:
    service = ActiveStrategiesService(session=session)
    result = service.list_active_strategies()

    return success_response(
        data=ActiveStrategiesResponse(strategies=result),
        request_id=request_id,
    )


@router.put(
    "/{strategy_id}/allocation",
    response_model=SuccessEnvelope[StrategyAllocationUpdateResponse],
    status_code=status.HTTP_200_OK,
)
def update_strategy_allocation(
    strategy_id: str,
    payload: StrategyAllocationUpdateRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_risk_manager_or_admin),
) -> SuccessEnvelope[StrategyAllocationUpdateResponse]:
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Allocation override reason is required.",
        )

    service = StrategyAllocationService(session=session)

    try:
        result = service.override_allocation(
            strategy_id=strategy_id,
            allocated_capital=payload.allocated_capital,
            reason=reason,
            updated_by=actor,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyAllocationUpdateResponse(
            strategy_id=result.strategy_id,
            allocated_capital=result.allocated_capital,
            total_portfolio_capital=result.total_portfolio_capital,
            reason=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )


@router.put(
    "/{strategy_id}/enabled",
    response_model=SuccessEnvelope[StrategyEnabledUpdateResponse],
    status_code=status.HTTP_200_OK,
)
def update_strategy_enabled(
    strategy_id: str,
    payload: StrategyEnabledUpdateRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[StrategyEnabledUpdateResponse]:
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Strategy enablement reason is required.",
        )

    service = StrategyControlService(session=session)

    try:
        result = service.set_enabled(
            strategy_id=strategy_id,
            enabled=payload.enabled,
            reason=reason,
            updated_by=actor,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyEnabledUpdateResponse(
            strategy_id=result.strategy_id,
            enabled=result.enabled,
            status=result.status,
            reason=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )


@router.post(
    "/{strategy_id}/governance/transition",
    response_model=SuccessEnvelope[StrategyGovernanceTransitionResponse],
    status_code=status.HTTP_200_OK,
)
def transition_strategy_governance(
    strategy_id: str,
    payload: StrategyGovernanceTransitionRequest,
    request: Request,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyGovernanceTransitionResponse]:
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Strategy governance transition reason is required.",
        )

    actor = str(getattr(request.state, "user_id", None) or "unknown")
    actor_role = str(getattr(request.state, "role", None) or "")
    service = StrategyGovernanceService(session=session)

    try:
        result = service.transition(
            strategy_id=strategy_id,
            to_state=payload.to_state,
            reason=reason,
            updated_by=actor,
            actor_role=actor_role,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyGovernanceTransitionResponse(
            strategy_id=result.strategy_id,
            from_state=result.from_state,
            to_state=result.to_state,
            reason=result.reason,
            updated_by=result.updated_by,
            updated_at=result.updated_at,
        ),
        request_id=request_id,
    )

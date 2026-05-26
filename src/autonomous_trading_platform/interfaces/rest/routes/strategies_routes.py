from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from autonomous_trading_platform.api.errors import APIError, ErrorCode
from autonomous_trading_platform.application.services.active_strategies_service import (
    ActiveStrategiesService,
)
from autonomous_trading_platform.application.services.governance_exceptions import (
    MissingSourceRunError,
    PromotionCriteriaConfigurationError,
    PromotionRulesMissingError,
)
from autonomous_trading_platform.application.services.strategy_allocation_service import (
    StrategyAllocationService,
)
from autonomous_trading_platform.application.services.strategy_catalog_service import (
    ExperimentCatalogService,
    StrategyCatalogService,
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
    ExperimentCreateRequest,
    ExperimentCreateResponse,
    ExperimentDetailResponse,
    ExperimentListResponse,
    ExperimentStrategiesResponse,
    StrategyAllocationsListResponse,
    StrategyAllocationStateResponse,
    StrategyAllocationUpdateRequest,
    StrategyAllocationUpdateResponse,
    StrategyCompareRequest,
    StrategyCompareResponse,
    StrategyDetailResponse,
    StrategyEnabledUpdateRequest,
    StrategyEnabledUpdateResponse,
    StrategyEquityCurveResponse,
    StrategyGovernanceTransitionRequest,
    StrategyGovernanceTransitionResponse,
    StrategyListResponse,
    StrategyStatus,
)
from autonomous_trading_platform.portfolio.exceptions import AllocationBudgetExceededError

router = APIRouter(prefix="/strategies", tags=["strategies"])
experiments_router = APIRouter(prefix="/experiments", tags=["experiments"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.get(
    "",
    response_model=SuccessEnvelope[StrategyListResponse],
)
def get_strategies(
    status_filter: Annotated[StrategyStatus | None, Query(alias="status")] = None,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyListResponse]:
    service = StrategyCatalogService(session=session)
    result = service.list_strategies(status_filter=status_filter)

    return success_response(
        data=StrategyListResponse(strategies=result),
        request_id=request_id,
    )


@router.post(
    "/compare",
    response_model=SuccessEnvelope[StrategyCompareResponse],
    status_code=status.HTTP_200_OK,
)
def compare_strategies(
    payload: StrategyCompareRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyCompareResponse]:
    service = StrategyCatalogService(session=session)
    try:
        result = service.compare_strategies(strategy_ids=payload.strategy_ids)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyCompareResponse(**result),
        request_id=request_id,
    )


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


@router.get(
    "/allocations",
    response_model=SuccessEnvelope[StrategyAllocationsListResponse],
)
def get_strategy_allocations(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[StrategyAllocationsListResponse]:
    service = StrategyAllocationService(session=session)
    rows = service.get_allocations_for_active_strategies()
    return success_response(
        data=StrategyAllocationsListResponse(
            strategies=[StrategyAllocationStateResponse(**r) for r in rows]
        ),
        request_id=request_id,
    )


@router.get(
    "/{strategy_id}",
    response_model=SuccessEnvelope[StrategyDetailResponse],
)
def get_strategy_detail(
    strategy_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyDetailResponse]:
    service = StrategyCatalogService(session=session)
    try:
        result = service.get_strategy_detail(strategy_id=strategy_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyDetailResponse(**result),
        request_id=request_id,
    )


@router.get(
    "/{strategy_id}/equity-curve",
    response_model=SuccessEnvelope[StrategyEquityCurveResponse],
)
def get_strategy_equity_curve(
    strategy_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[StrategyEquityCurveResponse]:
    service = StrategyCatalogService(session=session)
    try:
        result = service.get_strategy_equity_curve(strategy_id=strategy_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyEquityCurveResponse(**result),
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
            allocation_pct=payload.allocation_pct,
            reason=reason,
            updated_by=actor,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except AllocationBudgetExceededError as exc:
        raise APIError(
            code=ErrorCode.VALIDATION_ERROR,
            message=str(exc),
            http_status=status.HTTP_409_CONFLICT,
            details={
                "requested_strategy_id": exc.strategy_id,
                "requested_allocation_pct": exc.requested_pct,
                "resulting_total_pct": exc.resulting_total_pct,
                "configured_max_pct": exc.configured_max_pct,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return success_response(
        data=StrategyAllocationUpdateResponse(
            strategy_id=result.strategy_id,
            allocation_pct=result.allocation_pct,
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
            source_run_id=str(payload.source_run_id) if payload.source_run_id else None,
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
    except MissingSourceRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Promotion of strategy {exc.strategy_id!r} to {exc.target_state!r} requires "
                "an explicit source_run_id. Capital-bearing transitions must reference the "
                "specific evaluation run that justifies the promotion. "
                "Missing field: source_run_id."
            ),
        ) from exc
    except PromotionRulesMissingError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Promotion blocked: no active PromotionRules configured for "
                f"{exc.from_state!r} -> {exc.to_state!r}. "
                "Create a rule row with all required criteria to enable this transition."
            ),
        ) from exc
    except PromotionCriteriaConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Promotion blocked: PromotionRules (rule_id={exc.rule_id!r}) for "
                f"{exc.from_state!r} -> {exc.to_state!r} has null required criteria: "
                f"{exc.missing_fields}. Set these thresholds to unblock the transition."
            ),
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


@experiments_router.post(
    "",
    response_model=SuccessEnvelope[ExperimentCreateResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_experiment(
    payload: ExperimentCreateRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[ExperimentCreateResponse]:
    service = ExperimentCatalogService(session=session)
    try:
        result = service.create_experiment(
            name=payload.name,
            experiment_type=payload.experiment_type,
            symbols=payload.symbols,
            start_date=payload.start_date,
            end_date=payload.end_date,
            price_basis=payload.price_basis,
            strategy_count=payload.strategy_count,
            parameter_ranges=payload.parameter_ranges,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return success_response(
        data=ExperimentCreateResponse(**result),
        request_id=request_id,
    )


@experiments_router.get(
    "",
    response_model=SuccessEnvelope[ExperimentListResponse],
)
def get_experiments(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ExperimentListResponse]:
    service = ExperimentCatalogService(session=session)
    return success_response(
        data=ExperimentListResponse(experiments=service.list_experiments()),
        request_id=request_id,
    )


@experiments_router.post(
    "/{experiment_id}/cancel",
    response_model=SuccessEnvelope[ExperimentCreateResponse],
    status_code=status.HTTP_200_OK,
)
def cancel_experiment(
    experiment_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = Depends(require_operator_or_admin),
) -> SuccessEnvelope[ExperimentCreateResponse]:
    service = ExperimentCatalogService(session=session)
    try:
        service.cancel_experiment(experiment_id=experiment_id)
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
        data=ExperimentCreateResponse(experiment_id=experiment_id, status="cancelled"),
        request_id=request_id,
    )


@experiments_router.get(
    "/{experiment_id}/strategies",
    response_model=SuccessEnvelope[ExperimentStrategiesResponse],
)
def get_experiment_strategies(
    experiment_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ExperimentStrategiesResponse]:
    service = ExperimentCatalogService(session=session)
    try:
        strategies = service.get_experiment_strategies(experiment_id=experiment_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return success_response(
        data=ExperimentStrategiesResponse(
            experiment_id=experiment_id,
            strategies=strategies,
        ),
        request_id=request_id,
    )


@experiments_router.get(
    "/{experiment_id}",
    response_model=SuccessEnvelope[ExperimentDetailResponse],
)
def get_experiment_detail(
    experiment_id: str,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
) -> SuccessEnvelope[ExperimentDetailResponse]:
    service = ExperimentCatalogService(session=session)
    try:
        result = service.get_experiment_detail(experiment_id=experiment_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return success_response(
        data=ExperimentDetailResponse(**result),
        request_id=request_id,
    )

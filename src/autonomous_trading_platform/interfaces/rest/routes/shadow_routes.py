from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import get_request_id, require_operator_or_admin
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.shadow_runtime_validation_service import (
    ShadowRuntimeValidationService,
)
from autonomous_trading_platform.contracts.shadow.shadow_run import ShadowRunRequest
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.shadow_schemas import (
    PromotionEligibilityResponse,
    ShadowDivergenceListResponse,
    ShadowDivergenceResponse,
    ShadowRunListResponse,
    ShadowRunManifestResponse,
    ShadowValidationSummaryResponse,
    StartShadowRunRequest,
)

router = APIRouter(prefix="/shadow", tags=["shadow"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)


@router.post(
    "/runs",
    response_model=SuccessEnvelope[ShadowRunManifestResponse],
    status_code=201,
)
def start_shadow_run(
    body: StartShadowRunRequest,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ShadowRunManifestResponse]:
    service = ShadowRuntimeValidationService(session=session)
    manifest = service.start_shadow_run(
        ShadowRunRequest(
            simulation_run_id=body.simulation_run_id,
            live_run_id=body.live_run_id,
            shadow_mode_type=body.shadow_mode_type,
            strategy_id=body.strategy_id,
            covariance_snapshot_ref=body.covariance_snapshot_ref,
            factor_snapshot_ref=body.factor_snapshot_ref,
            allocation_config_hash=body.allocation_config_hash,
            optimizer_run_ids=body.optimizer_run_ids,
            required_passing_cycles=body.required_passing_cycles,
            metadata=body.metadata,
        )
    )
    session.commit()
    return success_response(
        data=_manifest_to_response(manifest),
        request_id=request_id,
    )


@router.get(
    "/runs",
    response_model=SuccessEnvelope[ShadowRunListResponse],
)
def list_shadow_runs(
    strategy_id: Annotated[str, Query()],
    status: Annotated[str | None, Query()] = None,
    validation_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ShadowRunListResponse]:
    service = ShadowRuntimeValidationService(session=session)
    manifests = service.list_shadow_runs(
        strategy_id,
        status=status,
        validation_status=validation_status,
        limit=limit,
    )
    return success_response(
        data=ShadowRunListResponse(
            runs=[_manifest_to_response(m) for m in manifests],
            total=len(manifests),
        ),
        request_id=request_id,
    )


@router.get(
    "/runs/{shadow_run_id}",
    response_model=SuccessEnvelope[ShadowRunManifestResponse],
)
def get_shadow_run(
    shadow_run_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ShadowRunManifestResponse]:
    service = ShadowRuntimeValidationService(session=session)
    manifest = service.get_shadow_run(shadow_run_id)
    if manifest is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Shadow run {shadow_run_id} not found")
    return success_response(
        data=_manifest_to_response(manifest),
        request_id=request_id,
    )


@router.post(
    "/runs/{shadow_run_id}/finalize",
    response_model=SuccessEnvelope[ShadowValidationSummaryResponse],
)
def finalize_shadow_run(
    shadow_run_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ShadowValidationSummaryResponse]:
    service = ShadowRuntimeValidationService(session=session)
    summary = service.finalize_shadow_run(shadow_run_id)
    session.commit()
    return success_response(
        data=ShadowValidationSummaryResponse(
            shadow_run_id=summary.shadow_run_id,
            live_run_id=summary.live_run_id,
            simulation_run_id=summary.simulation_run_id,
            strategy_id=summary.strategy_id,
            shadow_mode_type=summary.shadow_mode_type.value,
            validation_status=summary.validation_status.value,
            total_divergences=summary.total_divergences,
            threshold_exceedances=summary.threshold_exceedances,
            promotion_eligible=summary.promotion_eligible,
            divergence_by_category=summary.divergence_by_category,
            started_at=summary.started_at,
            completed_at=summary.completed_at,
            duration_seconds=summary.duration_seconds,
        ),
        request_id=request_id,
    )


@router.get(
    "/runs/{shadow_run_id}/divergences",
    response_model=SuccessEnvelope[ShadowDivergenceListResponse],
)
def list_divergences(
    shadow_run_id: UUID,
    category: Annotated[str | None, Query()] = None,
    exceeds_threshold_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[ShadowDivergenceListResponse]:
    service = ShadowRuntimeValidationService(session=session)
    rows = service.list_divergences(
        shadow_run_id,
        category=category,
        exceeds_threshold_only=exceeds_threshold_only,
        limit=limit,
    )
    return success_response(
        data=ShadowDivergenceListResponse(
            divergences=[
                ShadowDivergenceResponse(
                    divergence_id=r.divergence_id,
                    shadow_run_id=r.shadow_run_id,
                    timestamp=r.timestamp,
                    divergence_type=r.divergence_type,
                    category=r.category,
                    metric_name=r.metric_name,
                    symbol=r.symbol,
                    strategy_id=r.strategy_id,
                    sim_value=r.sim_value,
                    live_value=r.live_value,
                    divergence_magnitude=r.divergence_magnitude,
                    threshold=r.threshold,
                    exceeds_threshold=r.exceeds_threshold,
                    details=r.details or {},
                )
                for r in rows
            ],
            total=len(rows),
            shadow_run_id=str(shadow_run_id),
        ),
        request_id=request_id,
    )


@router.get(
    "/runs/{shadow_run_id}/promotion-eligibility",
    response_model=SuccessEnvelope[PromotionEligibilityResponse],
)
def check_promotion_eligibility(
    shadow_run_id: UUID,
    required_cycles: Annotated[int | None, Query(ge=1)] = None,
    request_id: Annotated[str, Depends(get_request_id)] = "",
    session: Annotated[Session, Depends(get_session)] = None,
    _actor: Annotated[str, Depends(require_operator_or_admin)] = "",
) -> SuccessEnvelope[PromotionEligibilityResponse]:
    service = ShadowRuntimeValidationService(session=session)
    manifest = service.get_shadow_run(shadow_run_id)
    if manifest is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"Shadow run {shadow_run_id} not found")
    eligible = service.check_promotion_eligibility(shadow_run_id, required_cycles=required_cycles)
    return success_response(
        data=PromotionEligibilityResponse(
            shadow_run_id=shadow_run_id,
            strategy_id=manifest.strategy_id,
            promotion_eligible=eligible,
            validation_status=manifest.validation_status.value,
            threshold_exceedances=manifest.threshold_exceedances,
        ),
        request_id=request_id,
    )


def _manifest_to_response(m) -> ShadowRunManifestResponse:
    return ShadowRunManifestResponse(
        shadow_run_id=m.shadow_run_id,
        live_run_id=m.live_run_id,
        simulation_run_id=m.simulation_run_id,
        strategy_id=m.strategy_id,
        shadow_mode_type=m.shadow_mode_type.value,
        status=m.status.value,
        validation_status=m.validation_status.value,
        total_divergences=m.total_divergences,
        threshold_exceedances=m.threshold_exceedances,
        promotion_eligible=m.promotion_eligible,
        config_hash=m.config_hash,
        covariance_snapshot_ref=m.covariance_snapshot_ref,
        factor_snapshot_ref=m.factor_snapshot_ref,
        allocation_config_hash=m.allocation_config_hash,
        optimizer_run_ids=m.optimizer_run_ids,
        started_at=m.started_at,
        completed_at=m.completed_at,
        duration_seconds=m.duration_seconds,
    )

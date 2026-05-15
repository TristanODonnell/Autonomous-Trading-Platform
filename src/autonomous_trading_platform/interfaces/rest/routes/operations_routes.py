from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from autonomous_trading_platform.api.dependencies import (
    get_request_id,
    require_operator_or_admin,
)
from autonomous_trading_platform.api.envelope import SuccessEnvelope, success_response
from autonomous_trading_platform.application.services.operational_alert_service import (
    OperationalAlertService,
)
from autonomous_trading_platform.application.services.operations_service import (
    OperationsService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.alert_schemas import (
    AlertActionRequest,
    AlertNoteRequest,
    AlertSnoozeRequest,
    CreateOperationalAlertRequest,
    OperationalAlertResponse,
    OperationalAlertsResponse,
)
from autonomous_trading_platform.interfaces.rest.schemas.operations_schemas import (
    OperationsJobRunsResponse,
    OperationsJobsResponse,
    OperationsRuntimeStateResponse,
)

router = APIRouter(prefix="/operations", tags=["operations"])

_request_id_dependency = Depends(get_request_id)
_session_dependency = Depends(get_session)
_operator_dependency = Depends(require_operator_or_admin)


def _alert_response(alert) -> OperationalAlertResponse:
    return OperationalAlertResponse(
        alert_id=alert.alert_id,
        fingerprint=alert.fingerprint,
        alert_name=alert.alert_name,
        category=alert.category,
        severity=alert.severity,
        status=alert.status,
        source=alert.source,
        environment=alert.environment,
        job_name=alert.job_name,
        strategy_id=alert.strategy_id,
        summary=alert.summary,
        details=alert.details,
        recommended_action=alert.recommended_action,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        acknowledged_at=alert.acknowledged_at,
        acknowledged_by=alert.acknowledged_by,
        resolved_at=alert.resolved_at,
        resolved_by=alert.resolved_by,
        snoozed_at=alert.snoozed_at,
        snoozed_by=alert.snoozed_by,
        snoozed_until=alert.snoozed_until,
        notes=alert.notes,
    )


def _alert_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    raise exc


@router.get(
    "/jobs",
    response_model=SuccessEnvelope[OperationsJobsResponse],
    status_code=status.HTTP_200_OK,
)
def list_operations_jobs(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsJobsResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsJobsResponse(jobs=service.list_jobs()),
        request_id=request_id,
    )


@router.get(
    "/jobs/{job_name}/runs",
    response_model=SuccessEnvelope[OperationsJobRunsResponse],
    status_code=status.HTTP_200_OK,
)
def list_operations_job_runs(
    job_name: str,
    limit: int = Query(default=20, ge=1, le=100),
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsJobRunsResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsJobRunsResponse(
            job_name=job_name,
            runs=service.list_job_runs(job_name=job_name, limit=limit),
        ),
        request_id=request_id,
    )


@router.get(
    "/runtime-state",
    response_model=SuccessEnvelope[OperationsRuntimeStateResponse],
    status_code=status.HTTP_200_OK,
)
def get_operations_runtime_state(
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationsRuntimeStateResponse]:
    service = OperationsService(session=session)
    return success_response(
        data=OperationsRuntimeStateResponse(**service.get_runtime_state()),
        request_id=request_id,
    )


@router.get(
    "/alerts",
    response_model=SuccessEnvelope[OperationalAlertsResponse],
    status_code=status.HTTP_200_OK,
)
def list_operational_alerts(
    status_filter: str | None = Query(default=None, alias="status"),
    severity: str | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertsResponse]:
    service = OperationalAlertService(session=session)
    try:
        alerts = service.list_alerts(
            status=status_filter,
            severity=severity,
            category=category,
            limit=limit,
        )
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(
        data=OperationalAlertsResponse(alerts=[_alert_response(alert) for alert in alerts]),
        request_id=request_id,
    )


@router.post(
    "/alerts",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_operational_alert(
    payload: CreateOperationalAlertRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    _actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.create_or_update_alert(**payload.model_dump())
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)


@router.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_200_OK,
)
def acknowledge_operational_alert(
    alert_id: str,
    payload: AlertActionRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.acknowledge_alert(alert_id=alert_id, actor=actor, note=payload.note)
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)


@router.post(
    "/alerts/{alert_id}/resolve",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_200_OK,
)
def resolve_operational_alert(
    alert_id: str,
    payload: AlertActionRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.resolve_alert(alert_id=alert_id, actor=actor, note=payload.note)
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)


@router.post(
    "/alerts/{alert_id}/snooze",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_200_OK,
)
def snooze_operational_alert(
    alert_id: str,
    payload: AlertSnoozeRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.snooze_alert(
            alert_id=alert_id,
            actor=actor,
            snoozed_until=payload.snoozed_until,
            note=payload.note,
        )
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)


@router.post(
    "/alerts/{alert_id}/unsnooze",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_200_OK,
)
def unsnooze_operational_alert(
    alert_id: str,
    payload: AlertActionRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.unsnooze_alert(alert_id=alert_id, actor=actor, note=payload.note)
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)


@router.post(
    "/alerts/{alert_id}/notes",
    response_model=SuccessEnvelope[OperationalAlertResponse],
    status_code=status.HTTP_200_OK,
)
def add_operational_alert_note(
    alert_id: str,
    payload: AlertNoteRequest,
    request_id: str = _request_id_dependency,
    session: Session = _session_dependency,
    actor: str = _operator_dependency,
) -> SuccessEnvelope[OperationalAlertResponse]:
    service = OperationalAlertService(session=session)
    try:
        alert = service.add_note(alert_id=alert_id, actor=actor, note=payload.note)
    except Exception as exc:
        raise _alert_error(exc) from exc
    return success_response(data=_alert_response(alert), request_id=request_id)

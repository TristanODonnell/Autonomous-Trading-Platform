from fastapi import APIRouter, HTTPException

from autonomous_trading_platform.application.services.dataset_version_command_service import (
    DatasetVersionCommandService,
)
from autonomous_trading_platform.application.services.feature_dataset_command_service import (
    FeatureDatasetCommandService,
)
from autonomous_trading_platform.application.services.ingestion_run_command_service import (
    IngestionRunCommandService,
)
from autonomous_trading_platform.db import get_session
from autonomous_trading_platform.interfaces.rest.schemas.dataset_version_schemas import (
    CreateDatasetVersionRequest,
)
from autonomous_trading_platform.interfaces.rest.schemas.feature_dataset_version_schema import (
    CreateFeatureDatasetVersionRequest,
)
from autonomous_trading_platform.interfaces.rest.schemas.ingestion_run_schemas import (
    CreateIngestionRunRequest,
    FailIngestionRunRequest,
    MetadataActionResponse,
)

router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.post("/dataset-versions", response_model=MetadataActionResponse)
def create_dataset_version(payload: CreateDatasetVersionRequest) -> MetadataActionResponse:
    service = DatasetVersionCommandService(session_factory=get_session)
    dataset_version_id = service.create_dataset_version(
        dataset_version_id=payload.dataset_version_id,
        dataset_name=payload.dataset_name,
        source=payload.source,
        price_basis=payload.price_basis,
        interval=payload.interval,
        schema_version=payload.schema_version,
        validation_status=payload.validation_status,
        source_manifest=payload.source_manifest,
        metadata_json=payload.metadata_json,
        symbol_coverage=payload.symbol_coverage,
        date_coverage_start=payload.date_coverage_start,
        date_coverage_end=payload.date_coverage_end,
        checksum=payload.checksum,
        created_at=payload.created_at,
    )
    return MetadataActionResponse(message=f"Dataset version created: {dataset_version_id}")


@router.post("/ingestion-runs", response_model=MetadataActionResponse)
def create_ingestion_run(payload: CreateIngestionRunRequest) -> MetadataActionResponse:
    service = IngestionRunCommandService(session_factory=get_session)
    ingestion_run_id = service.create_ingestion_run(
        ingestion_run_id=payload.ingestion_run_id,
        run_timestamp=payload.run_timestamp,
        run_type=payload.run_type,
        source=payload.source,
        dataset_version=payload.dataset_version,
        status=payload.status,
    )
    return MetadataActionResponse(message=f"Ingestion run created: {ingestion_run_id}")


@router.patch(
    "/ingestion-runs/{ingestion_run_id}/complete",
    response_model=MetadataActionResponse,
)
def complete_ingestion_run(ingestion_run_id: str) -> MetadataActionResponse:
    service = IngestionRunCommandService(session_factory=get_session)
    try:
        service.mark_ingestion_run_completed(ingestion_run_id=ingestion_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return MetadataActionResponse(message=f"Ingestion run marked complete: {ingestion_run_id}")


@router.patch(
    "/ingestion-runs/{ingestion_run_id}/fail",
    response_model=MetadataActionResponse,
)
def fail_ingestion_run(
    ingestion_run_id: str,
    payload: FailIngestionRunRequest,
) -> MetadataActionResponse:
    service = IngestionRunCommandService(session_factory=get_session)
    try:
        service.mark_ingestion_run_failed(
            ingestion_run_id=ingestion_run_id,
            error_message=payload.error_message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return MetadataActionResponse(message=f"Ingestion run marked failed: {ingestion_run_id}")


@router.post("/feature-dataset-versions", response_model=MetadataActionResponse)
def create_feature_dataset_version(
    payload: CreateFeatureDatasetVersionRequest,
) -> MetadataActionResponse:
    service = FeatureDatasetCommandService(session_factory=get_session)
    dataset_version_id = service.create_feature_dataset_version(
        dataset_version_id=payload.dataset_version_id,
        feature_name=payload.feature_name,
        dataset_name=payload.dataset_name,
        underlying_price_basis=payload.underlying_price_basis,
        underlying_dataset_version=payload.underlying_dataset_version,
        schema_version=payload.schema_version,
        validation_status=payload.validation_status,
        computation_parameters=payload.computation_parameters,
        computation_code_version=payload.computation_code_version,
        storage_path=payload.storage_path,
        source_manifest=payload.source_manifest,
        metadata_json=payload.metadata_json,
        symbol_coverage=payload.symbol_coverage,
        date_coverage_start=payload.date_coverage_start,
        date_coverage_end=payload.date_coverage_end,
        checksum=payload.checksum,
        created_at=payload.created_at,
    )
    return MetadataActionResponse(message=f"Feature dataset version created: {dataset_version_id}")

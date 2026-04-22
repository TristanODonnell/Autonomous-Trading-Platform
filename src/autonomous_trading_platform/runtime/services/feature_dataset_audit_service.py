from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.common.types import UTCDateTime
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


@dataclass(frozen=True)
class FeatureDatasetAuditRecord:
    feature_dataset_version_id: str
    feature_name: str
    dataset_name: str
    created_at: UTCDateTime
    schema_version: str

    source_dataset_version: str
    underlying_price_basis: PriceBasis

    computation_parameters: dict[str, Any] | None
    computation_code_version: str

    storage_path: str
    symbol_coverage: int | None
    date_coverage_start: date | None
    date_coverage_end: date | None

    validation_status: str
    checksum: str | None
    source_manifest: dict[str, Any] | None
    metadata_json: dict[str, Any] | None

    source_dataset: DatasetVersion | None


class FeatureDatasetAuditNotFoundError(Exception):
    def __init__(self, feature_dataset_version_id: str) -> None:
        self.feature_dataset_version_id = feature_dataset_version_id
        super().__init__(f"Feature dataset version '{feature_dataset_version_id}' was not found")


class FeatureDatasetAuditService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def inspect_feature_dataset(
        self,
        *,
        feature_dataset_version_id: str,
    ) -> FeatureDatasetAuditRecord:
        with SorUnitOfWork(self.session) as uow:
            feature_row = uow.feature_dataset_versions.get_by_dataset_version_id(
                feature_dataset_version_id
            )
            if feature_row is None:
                raise FeatureDatasetAuditNotFoundError(feature_dataset_version_id)

            source_dataset_row = uow.dataset_versions.get_by_dataset_version_id(
                feature_row.source_dataset_version
            )

            source_dataset = (
                None
                if source_dataset_row is None
                else uow.dataset_versions.to_contract(source_dataset_row)
            )

            return FeatureDatasetAuditRecord(
                feature_dataset_version_id=feature_row.dataset_version_id,
                feature_name=feature_row.feature_name,
                dataset_name=feature_row.dataset_name,
                created_at=feature_row.created_at,
                schema_version=feature_row.schema_version,
                source_dataset_version=feature_row.source_dataset_version,
                underlying_price_basis=feature_row.underlying_price_basis,
                computation_parameters=feature_row.computation_parameters,
                computation_code_version=feature_row.computation_code_version,
                storage_path=feature_row.storage_path,
                symbol_coverage=feature_row.symbol_coverage,
                date_coverage_start=feature_row.date_coverage_start,
                date_coverage_end=feature_row.date_coverage_end,
                validation_status=feature_row.validation_status,
                checksum=feature_row.checksum,
                source_manifest=feature_row.source_manifest,
                metadata_json=feature_row.metadata_json,
                source_dataset=source_dataset,
            )

    def list_feature_datasets_for_source_dataset(
        self,
        *,
        source_dataset_version: str,
        validated_only: bool = False,
    ) -> list[FeatureDatasetAuditRecord]:
        with SorUnitOfWork(self.session) as uow:
            if validated_only:
                feature_rows = (
                    uow.feature_dataset_versions.list_validated_by_source_dataset_version(
                        source_dataset_version=source_dataset_version
                    )
                )
            else:
                feature_rows = uow.feature_dataset_versions.list_by_source_dataset_version(
                    source_dataset_version=source_dataset_version
                )

            source_dataset_row = uow.dataset_versions.get_by_dataset_version_id(
                source_dataset_version
            )
            source_dataset = (
                None
                if source_dataset_row is None
                else uow.dataset_versions.to_contract(source_dataset_row)
            )

            results: list[FeatureDatasetAuditRecord] = []
            for row in feature_rows:
                results.append(
                    FeatureDatasetAuditRecord(
                        feature_dataset_version_id=row.dataset_version_id,
                        feature_name=row.feature_name,
                        dataset_name=row.dataset_name,
                        created_at=row.created_at,
                        schema_version=row.schema_version,
                        source_dataset_version=row.source_dataset_version,
                        underlying_price_basis=row.underlying_price_basis,
                        computation_parameters=row.computation_parameters,
                        computation_code_version=row.computation_code_version,
                        storage_path=row.storage_path,
                        symbol_coverage=row.symbol_coverage,
                        date_coverage_start=row.date_coverage_start,
                        date_coverage_end=row.date_coverage_end,
                        validation_status=row.validation_status,
                        checksum=row.checksum,
                        source_manifest=row.source_manifest,
                        metadata_json=row.metadata_json,
                        source_dataset=source_dataset,
                    )
                )

            return results

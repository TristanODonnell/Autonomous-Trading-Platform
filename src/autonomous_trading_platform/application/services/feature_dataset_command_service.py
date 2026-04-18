from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.runtime.services.feature_dataset_registration_service import (
    FeatureDatasetRegistrationService,
)


class FeatureDatasetCommandService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def create_feature_dataset_version(
        self,
        *,
        dataset_version_id: str | None,
        feature_name: str,
        dataset_name: str,
        underlying_price_basis: PriceBasis,
        underlying_dataset_version: str,
        schema_version: str,
        validation_status: str,
        computation_parameters: dict,
        storage_path: str,
        source_manifest: dict | None = None,
        metadata_json: dict | None = None,
        symbol_coverage: int | None = None,
        date_coverage_start: date | None = None,
        date_coverage_end: date | None = None,
        checksum: str | None = None,
        created_at: datetime | None = None,
    ) -> str:
        session = self.session_factory()
        try:
            registration_service = FeatureDatasetRegistrationService(session=session)

            version_id = dataset_version_id or self._generate_feature_dataset_version_id(
                feature_name=feature_name,
            )

            contract = FeatureDatasetVersion(
                dataset_version_id=version_id,
                feature_name=feature_name,
                dataset_name=dataset_name,
                created_at=created_at or datetime.now(UTC),
                schema_version=schema_version,
                underlying_dataset_version=underlying_dataset_version,
                underlying_price_basis=underlying_price_basis,
                computation_parameters=computation_parameters,
                storage_path=storage_path,
                symbol_coverage=symbol_coverage,
                date_coverage_start=date_coverage_start,
                date_coverage_end=date_coverage_end,
                validation_status=validation_status,
                checksum=checksum,
                source_manifest=source_manifest,
                metadata_json=metadata_json,
            )

            registration_service.register(contract)
            return version_id
        finally:
            session.close()

    def _generate_feature_dataset_version_id(
        self,
        *,
        feature_name: str,
    ) -> str:
        safe_feature_name = feature_name.strip().lower().replace(" ", "_")
        return f"feat_{safe_feature_name}_{uuid4().hex[:12]}"

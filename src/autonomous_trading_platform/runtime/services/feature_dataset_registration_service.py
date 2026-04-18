from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.feature_dataset_version import (
    FeatureDatasetVersion,
)
from autonomous_trading_platform.runtime.services.feature_dataset_validation_service import (
    FeatureDatasetValidationService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class FeatureDatasetRegistrationValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Feature dataset registration validation failed")


class FeatureDatasetAlreadyExistsError(Exception):
    def __init__(self, dataset_version_id: str) -> None:
        self.dataset_version_id = dataset_version_id
        super().__init__(
            f"Feature dataset version '{dataset_version_id}' already exists; registration must not overwrite prior versions"
        )


class FeatureDatasetRegistrationService:
    def __init__(
        self,
        session: Session,
        feature_validation_service: FeatureDatasetValidationService | None = None,
    ) -> None:
        self.session = session
        self.validation_service = feature_validation_service or FeatureDatasetValidationService()

    def register(self, feature_dataset_version: FeatureDatasetVersion) -> FeatureDatasetVersion:
        validation_result = self.validation_service.validate_feature_dataset(
            feature_dataset_version
        )

        if not validation_result.ok:
            errors = [violation.message for violation in validation_result.violations]
            raise FeatureDatasetRegistrationValidationError(errors)

        with SorUnitOfWork(self.session) as uow:
            existing = uow.feature_dataset_versions.get_by_dataset_version_id(
                feature_dataset_version.dataset_version_id
            )
            if existing is not None:
                raise FeatureDatasetAlreadyExistsError(feature_dataset_version.dataset_version_id)

            row = uow.feature_dataset_versions.to_row(feature_dataset_version)
            uow.feature_dataset_versions.insert(row)
            return uow.feature_dataset_versions.to_contract(row)

    def save(self, feature_dataset_version: FeatureDatasetVersion) -> FeatureDatasetVersion:
        with SorUnitOfWork(self.session) as uow:
            row = uow.feature_dataset_versions.to_row(feature_dataset_version)
            uow.feature_dataset_versions.insert(row)
            return uow.feature_dataset_versions.to_contract(row)

    def get_latest_validated_dataset(
        self,
        *,
        feature_name: str,
        underlying_price_basis: PriceBasis,
    ) -> FeatureDatasetVersion | None:
        with SorUnitOfWork(self.session) as uow:
            row = uow.feature_dataset_versions.get_latest_validated(
                feature_name=feature_name,
                underlying_price_basis=underlying_price_basis,
            )
            if row is None:
                return None

            return uow.feature_dataset_versions.to_contract(row)

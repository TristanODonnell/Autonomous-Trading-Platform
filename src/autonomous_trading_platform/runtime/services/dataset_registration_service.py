from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.runtime.services.dataset_validation_service import (
    DatasetValidationService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class DatasetRegistrationValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Dataset registration validation failed")


class DatasetRegistrationService:
    def __init__(
        self,
        session: Session,
        validation_service: DatasetValidationService | None = None,
    ) -> None:
        self.session = session
        self.validation_service = validation_service or DatasetValidationService()

    def register(self, dataset_version: DatasetVersion) -> DatasetVersion:
        validation_result = self.validation_service.validate_dataset(dataset_version)

        if not validation_result.ok:
            errors = [violation.message for violation in validation_result.violations]
            raise DatasetRegistrationValidationError(errors)

        with SorUnitOfWork(self.session) as uow:
            row = uow.dataset_versions.to_row(dataset_version)
            saved_row = uow.dataset_versions.upsert(row)
            return uow.dataset_versions.to_contract(saved_row)

    def save(self, dataset_version: DatasetVersion) -> DatasetVersion:
        with SorUnitOfWork(self.session) as uow:
            row = uow.dataset_versions.to_row(dataset_version)
            saved_row = uow.dataset_versions.upsert(row)
            return uow.dataset_versions.to_contract(saved_row)

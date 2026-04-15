from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.ingestion_run import IngestionRun
from autonomous_trading_platform.runtime.services.ingestion_run_validation_service import (
    IngestionRunValidationService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class IngestionRunRegistrationValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Ingestion Run registration validation failed")


class IngestionRunRegistrationService:
    def __init__(
        self,
        session: Session,
        validation_service: IngestionRunValidationService | None = None,
    ) -> None:
        self.session = session
        self.validation_service = validation_service or IngestionRunValidationService()

    def register(self, ingestion_run: IngestionRun) -> IngestionRun:
        validation_result = self.validation_service.validate_ingestion_run(ingestion_run)

        if not validation_result.ok:
            errors = [violation.message for violation in validation_result.violations]
            raise IngestionRunRegistrationValidationError(errors)

        with SorUnitOfWork(self.session) as uow:
            row = uow.ingestion_runs.to_row(ingestion_run)
            saved_row = uow.ingestion_runs.upsert(row)
            return uow.ingestion_runs.to_contract(saved_row)

    def save(self, ingestion_run: IngestionRun) -> IngestionRun:
        with SorUnitOfWork(self.session) as uow:
            row = uow.ingestion_runs.to_row(ingestion_run)
            saved_row = uow.ingestion_runs.upsert(row)
            return uow.ingestion_runs.to_contract(saved_row)

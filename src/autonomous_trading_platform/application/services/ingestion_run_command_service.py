from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import RunType
from autonomous_trading_platform.storage.sor.models.ingestion_runs import IngestionRuns
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class IngestionRunCommandService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def create_ingestion_run(
        self,
        *,
        ingestion_run_id: str,
        run_timestamp: datetime,
        run_type: RunType,
        source: str,
        dataset_version: str,
        status: str,
    ) -> str:
        session = self.session_factory()
        try:
            with SorUnitOfWork(session) as uow:
                ingestion_run = IngestionRuns(
                    ingestion_run_id=ingestion_run_id,
                    created_at=datetime.now(UTC),
                    run_timestamp=run_timestamp,
                    run_type=run_type,
                    source=source,
                    dataset_version=dataset_version,
                    status=status,
                    started_at=datetime.now(UTC),
                )

                uow.ingestion_runs.insert(ingestion_run)

            return ingestion_run_id
        finally:
            session.close()

    def mark_ingestion_run_completed(
        self,
        *,
        ingestion_run_id: str,
    ) -> None:
        session = self.session_factory()
        try:
            with SorUnitOfWork(session) as uow:
                ingestion_run = uow.ingestion_runs.get_by_ingestion_run_id(ingestion_run_id)
                if ingestion_run is None:
                    raise ValueError(f"Ingestion run not found: {ingestion_run_id}")

                ingestion_run.status = "SUCCESS"
                ingestion_run.completed_at = datetime.now(UTC)
        finally:
            session.close()

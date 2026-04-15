from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import BarInterval, PriceBasis
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class DatasetVersionCommandService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def create_dataset_version(
        self,
        *,
        dataset_version_id: str,
        dataset_name: str,
        source: str,
        price_basis: PriceBasis | None,
        interval: BarInterval | None,
        schema_version: str,
        validation_status: str,
        source_manifest: dict | None = None,
        metadata_json: dict | None = None,
    ) -> str:
        session = self.session_factory()
        try:
            with SorUnitOfWork(session) as uow:
                dataset_version = DatasetVersions(
                    dataset_version_id=dataset_version_id,
                    dataset_name=dataset_name,
                    created_at=datetime.now(UTC),
                    source=source,
                    price_basis=price_basis,
                    interval=interval,
                    schema_version=schema_version,
                    validation_status=validation_status,
                    source_manifest=source_manifest,
                    metadata_json=metadata_json,
                )

                uow.dataset_versions.insert(dataset_version)

            return dataset_version_id
        finally:
            session.close()

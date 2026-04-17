from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class DatasetVersionQueryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_dataset_version(self, dataset_version_id: str) -> DatasetVersion | None:
        with SorUnitOfWork(self.session) as uow:
            row = uow.dataset_versions.get_by_dataset_version_id(dataset_version_id)
            if row is None:
                return None
            return uow.dataset_versions.to_contract(row)

    def get_latest_validated_dataset(
        self,
        *,
        dataset_name: str,
        price_basis: PriceBasis,
    ) -> DatasetVersion | None:
        with SorUnitOfWork(self.session) as uow:
            row = uow.dataset_versions.get_latest_validated(
                dataset_name=dataset_name,
                price_basis=price_basis,
            )
            if row is None:
                return None
            return uow.dataset_versions.to_contract(row)

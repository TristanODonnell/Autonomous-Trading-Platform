from datetime import date

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

    def get_datasets_for_experiment(
        self,
        *,
        dataset_name: str,
        price_basis: PriceBasis,
        start_date,
        end_date,
    ) -> list[DatasetVersion]:
        with SorUnitOfWork(self.session) as uow:
            rows = uow.dataset_versions.list_validated_by_coverage_and_price_basis(
                dataset_name=dataset_name,
                price_basis=price_basis,
                start_date=start_date,
                end_date=end_date,
            )

            return [uow.dataset_versions.to_contract(row) for row in rows]

    def get_datasets_for_symbol_range(
        self,
        *,
        dataset_name: str,
        symbol: str,
        start_date: date,
        end_date: date,
        price_basis: PriceBasis,
    ) -> list[DatasetVersion]:
        with SorUnitOfWork(self.session) as uow:
            dataset_version_ids = (
                uow.symbol_date_coverage.list_dataset_versions_covering_symbol_date_range(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
            )

            rows = uow.dataset_versions.list_validated_by_ids_and_price_basis(
                dataset_version_ids=dataset_version_ids,
                dataset_name=dataset_name,
                price_basis=price_basis,
            )

            return [uow.dataset_versions.to_contract(row) for row in rows]

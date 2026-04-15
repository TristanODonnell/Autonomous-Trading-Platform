from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.runtime.dataset_version import DatasetVersion
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class DatasetVersionsRepository(BaseRepository):
    def get_by_dataset_version_id(self, dataset_version_id: str) -> DatasetVersions | None:
        stmt = select(DatasetVersions).where(
            DatasetVersions.dataset_version_id == dataset_version_id
        )
        return cast(DatasetVersions | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: DatasetVersions) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[DatasetVersions]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: DatasetVersions) -> DatasetVersions:
        existing = self.get_by_dataset_version_id(row.dataset_version_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in DatasetVersions.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_dataset_version_id(self, dataset_version_id: str) -> None:
        obj = self.get_by_dataset_version_id(dataset_version_id)
        if obj is not None:
            self.session.delete(obj)

    def to_row(self, contract: DatasetVersion) -> DatasetVersions:
        return DatasetVersions(
            dataset_version_id=contract.dataset_version_id,
            dataset_name=contract.dataset_name,
            created_at=contract.created_at,
            source=contract.source,
            price_basis=contract.price_basis,
            interval=contract.interval,
            schema_version=contract.schema_version,
            symbol_coverage=contract.symbol_coverage,
            date_coverage_start=contract.date_coverage_start,
            date_coverage_end=contract.date_coverage_end,
            validation_status=contract.validation_status,
            checksum=contract.checksum,
            source_manifest=contract.source_manifest,
            metadata_json=contract.metadata_json,
        )

    def to_contract(self, row: DatasetVersions) -> DatasetVersion:
        return DatasetVersion(
            dataset_version_id=row.dataset_version_id,
            dataset_name=row.dataset_name,
            created_at=row.created_at,
            source=row.source,
            price_basis=row.price_basis,
            interval=row.interval,
            schema_version=row.schema_version,
            symbol_coverage=row.symbol_coverage,
            date_coverage_start=row.date_coverage_start,
            date_coverage_end=row.date_coverage_end,
            validation_status=row.validation_status,
            checksum=row.checksum,
            source_manifest=row.source_manifest,
            metadata_json=row.metadata_json,
        )

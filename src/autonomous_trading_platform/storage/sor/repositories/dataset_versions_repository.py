from typing import cast

from sqlalchemy import select

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

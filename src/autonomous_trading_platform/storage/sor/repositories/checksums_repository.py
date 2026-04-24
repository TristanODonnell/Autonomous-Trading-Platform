from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.checksums import Checksums
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class ChecksumsRepository(BaseRepository):
    def get_by_checksum_id(self, checksum_id: str) -> Checksums | None:
        stmt = select(Checksums).where(Checksums.checksum_id == checksum_id)
        return cast(Checksums | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: Checksums) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[Checksums]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: Checksums) -> Checksums:
        existing = self.get_by_checksum_id(row.checksum_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in Checksums.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_checksum_id(self, checksum_id: str) -> None:
        obj = self.get_by_checksum_id(checksum_id)
        if obj is not None:
            self.session.delete(obj)

    def get_by_dataset_version_and_object_path(
        self,
        *,
        dataset_version: str,
        object_path: str,
    ) -> Checksums | None:
        stmt = select(Checksums).where(
            Checksums.dataset_version == dataset_version,
            Checksums.object_path == object_path,
        )
        return cast(Checksums | None, self.session.scalars(stmt).one_or_none())

    def build_row(
        self,
        *,
        dataset_version: str,
        object_type: str,
        object_path: str,
        checksum_algorithm: str,
        checksum_value: str,
    ) -> Checksums:
        checksum_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{dataset_version}:{object_type}:{object_path}:{checksum_algorithm}",
            )
        )

        return Checksums(
            checksum_id=checksum_id,
            dataset_version=dataset_version,
            object_type=object_type,
            object_path=object_path,
            checksum_algorithm=checksum_algorithm,
            checksum_value=checksum_value,
            created_at=datetime.now(UTC),
        )

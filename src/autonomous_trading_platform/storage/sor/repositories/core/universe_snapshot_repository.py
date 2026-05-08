from datetime import date, datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.universe_snapshots import (
    UniverseSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class UniverseSnapshotRepository(BaseRepository):
    def get_by_universe_id(self, id_value: str) -> UniverseSnapshot | None:
        stmt = select(UniverseSnapshot).where(UniverseSnapshot.universe_id == id_value)
        result = cast(UniverseSnapshot | None, self.session.execute(stmt).scalar_one_or_none())
        return result

    def get_by_snapshot_date(self, snapshot_date: date) -> UniverseSnapshot | None:
        stmt = select(UniverseSnapshot).where(UniverseSnapshot.snapshot_date == snapshot_date)
        result = cast(UniverseSnapshot | None, self.session.execute(stmt).scalar_one_or_none())
        return result

    def get_open_snapshot(self) -> UniverseSnapshot | None:
        stmt = select(UniverseSnapshot).where(UniverseSnapshot.effective_end.is_(None))
        result = cast(UniverseSnapshot | None, self.session.execute(stmt).scalar_one_or_none())
        return result

    def get_effective_for_date(self, as_of: datetime) -> UniverseSnapshot | None:
        stmt = select(UniverseSnapshot).where(
            UniverseSnapshot.effective_start <= as_of,
            (UniverseSnapshot.effective_end.is_(None)) | (UniverseSnapshot.effective_end > as_of),
        )
        result = cast(UniverseSnapshot | None, self.session.execute(stmt).scalar_one_or_none())
        return result

    def get_previous_snapshot_before(self, as_of: datetime) -> UniverseSnapshot | None:
        stmt = (
            select(UniverseSnapshot)
            .where(UniverseSnapshot.effective_start < as_of)
            .order_by(UniverseSnapshot.effective_start.desc())
        )
        result = cast(UniverseSnapshot | None, self.session.execute(stmt).scalars().first())
        return result

    def insert(self, row: UniverseSnapshot) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[UniverseSnapshot]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: UniverseSnapshot) -> UniverseSnapshot:
        existing = self.get_by_universe_id(str(row.universe_id))

        if existing is None:
            self.session.add(row)
            return row

        for column in UniverseSnapshot.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_universe_id(self, id_value: str) -> None:
        obj = self.get_by_universe_id(id_value)
        if obj is not None:
            self.session.delete(obj)

    def close_open_snapshot(self, effective_end: datetime) -> UniverseSnapshot | None:
        snapshot = self.get_open_snapshot()
        if snapshot is None:
            return None

        snapshot.effective_end = effective_end
        return snapshot

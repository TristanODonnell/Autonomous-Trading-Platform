from typing import cast

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PositionSnapshotRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_snapshot_id(self, id_value: str) -> PositionSnapshot | None:
        stmt = select(PositionSnapshot).where(PositionSnapshot.snapshot_id == id_value)
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest(self) -> PositionSnapshot | None:
        stmt = select(PositionSnapshot).order_by(desc(PositionSnapshot.timestamp)).limit(1)
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest_for_symbol(self, symbol: str) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .where(PositionSnapshot.symbol == symbol)
            .order_by(desc(PositionSnapshot.timestamp))
            .limit(1)
        )
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def list_recent_for_symbol(self, symbol: str, limit: int = 20) -> list[PositionSnapshot]:
        stmt = (
            select(PositionSnapshot)
            .where(PositionSnapshot.symbol == symbol)
            .order_by(desc(PositionSnapshot.timestamp))
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    # -----------------------------
    # Inserts
    # -----------------------------

    def insert(self, row: PositionSnapshot) -> None:
        """Insert a single row."""
        self.session.add(row)

    def insert_many(self, rows: list[PositionSnapshot]) -> None:
        """Insert multiple rows."""
        self.session.add_all(rows)

    # -----------------------------
    # Upserts
    # -----------------------------

    def upsert(self, row: PositionSnapshot) -> PositionSnapshot:
        """Insert or update.

        Uses a savepoint so that a UniqueViolation on uq_position_snapshots_run_ts_source
        (multiple fills in the same 5-min bar) rolls back only the savepoint and then
        falls back to updating the existing row — leaving the outer transaction intact.
        """
        existing = self.get_by_snapshot_id(row.snapshot_id)
        if existing is not None:
            existing.positions = row.positions
            return existing

        sp = self.session.begin_nested()
        try:
            self.session.add(row)
            sp.commit()
            return row
        except IntegrityError:
            sp.rollback()
            existing = self._get_by_run_ts_source(row.run_id, row.timestamp, row.source)
            if existing is not None:
                existing.positions = row.positions
                return existing
            raise

    def _get_by_run_ts_source(
        self,
        run_id: object,
        timestamp: object,
        source: object,
    ) -> PositionSnapshot | None:
        stmt = select(PositionSnapshot).where(
            PositionSnapshot.run_id == run_id,
            PositionSnapshot.timestamp == timestamp,
            PositionSnapshot.source == source,
        )
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_snapshot_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_snapshot_id(id_value)
        if obj is not None:
            self.session.delete(obj)

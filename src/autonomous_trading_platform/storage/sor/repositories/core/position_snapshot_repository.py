from typing import cast
from uuid import UUID

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

    def get_by_snapshot_id(self, id_value: str | UUID) -> PositionSnapshot | None:
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

    def get_or_create_header(
        self,
        snapshot_id: UUID,
        run_id: object,
        timestamp: object,
        source: object,
    ) -> PositionSnapshot:
        """Return the existing snapshot for (run_id, timestamp, source), or INSERT a new one.

        Does NOT touch .positions — caller is responsible for setting positions on the
        returned object.  Using a savepoint so a concurrent INSERT (same 5-min bar,
        multiple fills) degrades gracefully to a SELECT fallback without poisoning the
        outer transaction.
        """
        existing = self.get_by_snapshot_id(snapshot_id)
        if existing is not None:
            return existing

        sp = self.session.begin_nested()
        try:
            new_row = PositionSnapshot(
                snapshot_id=snapshot_id,
                run_id=run_id,
                timestamp=timestamp,
                source=source,
            )
            self.session.add(new_row)
            sp.commit()
            return new_row
        except IntegrityError:
            sp.rollback()
            existing = self._get_by_run_ts_source(run_id, timestamp, source)
            if existing is not None:
                return existing
            raise

    def upsert(self, row: PositionSnapshot) -> PositionSnapshot:
        """Insert or update.  Delegates to get_or_create_header then assigns positions."""
        target = self.get_or_create_header(
            snapshot_id=row.snapshot_id,
            run_id=row.run_id,
            timestamp=row.timestamp,
            source=row.source,
        )
        target.positions = row.positions
        return target

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

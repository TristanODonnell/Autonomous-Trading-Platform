from datetime import datetime
from typing import cast

from sqlalchemy import desc, select

from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class CashSnapshotRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_snapshot_id(self, id_value: str) -> CashSnapshot | None:
        stmt = select(CashSnapshot).where(CashSnapshot.snapshot_id == id_value)
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest(self) -> CashSnapshot | None:
        stmt = select(CashSnapshot).order_by(desc(CashSnapshot.timestamp)).limit(1)
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def list_recent(self, limit: int = 20) -> list[CashSnapshot]:
        stmt = select(CashSnapshot).order_by(desc(CashSnapshot.timestamp)).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    def list_since(
        self,
        start_timestamp: datetime,
    ) -> list[CashSnapshot]:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.timestamp >= start_timestamp)
            .where(CashSnapshot.equity.is_not(None))
            .order_by(CashSnapshot.timestamp.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_between(
        self,
        start_timestamp: datetime,
        end_timestamp: datetime,
    ) -> list[CashSnapshot]:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.timestamp >= start_timestamp)
            .where(CashSnapshot.timestamp <= end_timestamp)
            .where(CashSnapshot.equity.is_not(None))
            .order_by(CashSnapshot.timestamp.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    # -----------------------------
    # Inserts
    # -----------------------------

    def insert(self, row: CashSnapshot) -> None:
        """Insert a single row."""
        self.session.add(row)

    def insert_many(self, rows: list[CashSnapshot]) -> None:
        """Insert multiple rows."""
        self.session.add_all(rows)

    # -----------------------------
    # Upserts
    # -----------------------------

    def upsert(self, row: CashSnapshot) -> CashSnapshot:
        """
        Insert or update based on deterministic ID.
        """
        existing = self.get_by_snapshot_id(row.snapshot_id)

        if existing is None:
            self.session.add(row)
            return row

        # Update fields (explicit updates recommended)
        for column in CashSnapshot.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_snapshot_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_snapshot_id(id_value)
        if obj is not None:
            self.session.delete(obj)

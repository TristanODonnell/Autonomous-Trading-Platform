from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import desc, select

from autonomous_trading_platform.storage.sor.models.broker_account_snapshots import (
    BrokerAccountSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class BrokerAccountSnapshotRepository(BaseRepository):
    def get_by_snapshot_id(self, snapshot_id: UUID) -> BrokerAccountSnapshot | None:
        stmt = select(BrokerAccountSnapshot).where(BrokerAccountSnapshot.snapshot_id == snapshot_id)
        return cast(BrokerAccountSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest(self) -> BrokerAccountSnapshot | None:
        stmt = (
            select(BrokerAccountSnapshot)
            .order_by(
                desc(BrokerAccountSnapshot.observed_at),
                desc(BrokerAccountSnapshot.created_at),
                desc(BrokerAccountSnapshot.snapshot_id),
            )
            .limit(1)
        )
        return cast(BrokerAccountSnapshot | None, self.session.scalars(stmt).one_or_none())

    def upsert(self, row: BrokerAccountSnapshot) -> BrokerAccountSnapshot:
        existing = self.get_by_snapshot_id(row.snapshot_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in BrokerAccountSnapshot.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

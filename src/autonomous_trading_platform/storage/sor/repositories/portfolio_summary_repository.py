from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.position_snapshot_items import (
    PositionSnapshotItem,
)
from autonomous_trading_platform.storage.sor.models.position_snapshots import PositionSnapshot


class PortfolioSummaryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_latest_position_snapshot(self) -> PositionSnapshot | None:
        stmt = select(PositionSnapshot).order_by(desc(PositionSnapshot.timestamp)).limit(1)
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_latest_cash_snapshot(self) -> CashSnapshot | None:
        stmt = select(CashSnapshot).order_by(desc(CashSnapshot.timestamp)).limit(1)
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_prior_position_snapshot(self, before_timestamp: datetime) -> PositionSnapshot | None:
        stmt = (
            select(PositionSnapshot)
            .where(PositionSnapshot.timestamp < before_timestamp)
            .order_by(desc(PositionSnapshot.timestamp))
            .limit(1)
        )
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_first_position_snapshot(self) -> PositionSnapshot | None:
        stmt = select(PositionSnapshot).order_by(PositionSnapshot.timestamp.asc()).limit(1)
        return cast(PositionSnapshot | None, self.session.scalars(stmt).one_or_none())

    def get_total_market_value_for_snapshot(self, snapshot_id: UUID) -> Decimal:
        stmt = select(PositionSnapshotItem).where(PositionSnapshotItem.snapshot_id == snapshot_id)

        items = self.session.scalars(stmt).all()

        total = Decimal("0")
        for item in items:
            if item.market_value is not None:
                total += Decimal(item.market_value)

        return total

    def compute_total_equity(
        self, position_snapshot: PositionSnapshot, cash_snapshot: CashSnapshot
    ) -> Decimal:
        position_value = self.get_total_market_value_for_snapshot(position_snapshot.snapshot_id)
        cash = Decimal(cash_snapshot.cash)

        return position_value + cash

    def get_cash_snapshot_at_or_before(
        self,
        timestamp: datetime,
    ) -> CashSnapshot | None:
        stmt = (
            select(CashSnapshot)
            .where(CashSnapshot.timestamp <= timestamp)
            .order_by(desc(CashSnapshot.timestamp))
            .limit(1)
        )
        return cast(CashSnapshot | None, self.session.scalars(stmt).one_or_none())

from typing import cast

from sqlalchemy import desc, select

from autonomous_trading_platform.contracts.common.enums import OrderStatus
from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository

OPEN_STATUSES = {
    OrderStatus.NEW.value,
    OrderStatus.SUBMITTED.value,
    OrderStatus.PARTIALLY_FILLED.value,
}


class BrokerOrderRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_broker_order_id(self, id_value: str) -> BrokerOrder | None:
        stmt = select(BrokerOrder).where(BrokerOrder.broker_order_id == id_value)
        return cast(BrokerOrder | None, self.session.scalars(stmt).one_or_none())

    def get_latest_for_order_id(self, order_id: str) -> BrokerOrder | None:
        stmt = (
            select(BrokerOrder)
            .where(BrokerOrder.order_id == order_id)
            .order_by(desc(BrokerOrder.updated_at))
            .limit(1)
        )
        return cast(BrokerOrder | None, self.session.scalars(stmt).one_or_none())

    def list_open_orders(self) -> list[BrokerOrder]:
        stmt = (
            select(BrokerOrder)
            .where(BrokerOrder.status.in_(OPEN_STATUSES))
            .order_by(desc(BrokerOrder.updated_at))
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_recent(self, limit: int = 20) -> list[BrokerOrder]:
        stmt = select(BrokerOrder).order_by(desc(BrokerOrder.updated_at)).limit(limit)
        return list(self.session.execute(stmt).scalars().all())

    # -----------------------------
    # Inserts
    # -----------------------------

    def insert(self, row: BrokerOrder) -> None:
        """Insert a single row."""
        self.session.add(row)

    def insert_many(self, rows: list[BrokerOrder]) -> None:
        """Insert multiple rows."""
        self.session.add_all(rows)

    # -----------------------------
    # Upserts
    # -----------------------------

    def upsert(self, row: BrokerOrder) -> BrokerOrder:
        """
        Insert or update based on deterministic ID.
        """
        existing = self.get_by_broker_order_id(row.broker_order_id)

        if existing is None:
            self.session.add(row)
            return row

        # Update fields (explicit updates recommended)
        for column in BrokerOrder.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_broker_order_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_broker_order_id(id_value)
        if obj is not None:
            self.session.delete(obj)

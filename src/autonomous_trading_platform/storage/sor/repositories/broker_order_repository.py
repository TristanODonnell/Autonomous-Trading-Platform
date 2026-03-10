from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.broker_orders import BrokerOrder
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class BrokerOrderRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_broker_order_id(self, id_value: str) -> BrokerOrder | None:
        """Fetch a single row by deterministic ID."""
        stmt = select(BrokerOrder).where(BrokerOrder.broker_order_id == id_value)
        result: BrokerOrder | None = self.session.execute(stmt).scalar_one_or_none()
        return result

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

    def delete_by_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_broker_order_id(id_value)
        if obj is not None:
            self.session.delete(obj)

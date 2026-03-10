from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class CorporateActionRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_id(self, id_value: str) -> CorporateAction | None:
        """Fetch a single row by deterministic ID."""
        stmt = select(CorporateAction).where(CorporateAction.action_id == id_value)
        result: CorporateAction | None = self.session.execute(stmt).scalar_one_or_none()
        return result

    # -----------------------------
    # Inserts
    # -----------------------------

    def insert(self, row: CorporateAction) -> None:
        """Insert a single row."""
        self.session.add(row)

    def insert_many(self, rows: list[CorporateAction]) -> None:
        """Insert multiple rows."""
        self.session.add_all(rows)

    # -----------------------------
    # Upserts
    # -----------------------------

    def upsert(self, row: CorporateAction) -> CorporateAction:
        """
        Insert or update based on deterministic ID.
        """
        existing = self.get_by_id(row.action_id)

        if existing is None:
            self.session.add(row)
            return row

        # Update fields (explicit updates recommended)
        for column in CorporateAction.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_id(id_value)
        if obj is not None:
            self.session.delete(obj)

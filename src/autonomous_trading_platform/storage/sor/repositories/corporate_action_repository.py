from dataclasses import dataclass
from datetime import date
from typing import Generic, TypeVar, cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository

T = TypeVar("T")


@dataclass(frozen=True)
class UpsertResult(Generic[T]):
    entity: T
    created: bool


class CorporateActionRepository(BaseRepository):
    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_action_id(self, id_value: str) -> CorporateAction | None:
        """Fetch a single row by deterministic ID."""
        stmt = select(CorporateAction).where(CorporateAction.action_id == id_value)
        result: CorporateAction | None = self.session.execute(stmt).scalar_one_or_none()
        return result

    def get_actions_for_symbols_between(
        self,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        if not symbols:
            return []

        stmt = (
            select(CorporateAction)
            .where(
                CorporateAction.symbol.in_(symbols),
                CorporateAction.effective_date >= start_date,
                CorporateAction.effective_date <= end_date,
            )
            .order_by(CorporateAction.effective_date.asc(), CorporateAction.symbol.asc())
        )

        rows = self.session.execute(stmt).scalars().all()
        return cast(list[CorporateAction], rows)

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

    def upsert(self, row: CorporateAction) -> UpsertResult[CorporateAction]:
        existing = self.get_by_action_id(row.action_id)

        if existing is None:
            self.session.add(row)
            return UpsertResult(entity=row, created=True)

        updatable_columns = (
            "symbol",
            "action_type",
            "effective_date",
            "announced_date",
            "record_date",
            "payable_date",
            "cash_amount",
            "split_ratio",
            "currency",
            "new_symbol",
            "source",
            "ingested_at",
            "meta",
        )

        for name in updatable_columns:
            setattr(existing, name, getattr(row, name))

        return UpsertResult(entity=existing, created=False)

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_action_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_action_id(id_value)
        if obj is not None:
            self.session.delete(obj)

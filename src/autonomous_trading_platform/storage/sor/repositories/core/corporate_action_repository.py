from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Generic, TypeVar, cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.market.corporate_action import (
    CorporateAction as CorporateActionContract,
)
from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository

T = TypeVar("T")


@dataclass(frozen=True)
class UpsertResult(Generic[T]):
    entity: T
    created: bool


class CorporateActionRepository(BaseRepository):
    # -----------------------------
    # Contract <-> row mapping
    # -----------------------------

    @staticmethod
    def to_row(contract: CorporateActionContract) -> CorporateAction:
        return CorporateAction(
            action_id=contract.action_id,
            symbol=contract.symbol,
            action_type=contract.action_type,
            effective_date=contract.effective_date,
            announced_date=contract.announced_date,
            record_date=contract.record_date,
            payable_date=contract.payable_date,
            split_ratio=float(contract.split_ratio) if contract.split_ratio is not None else None,
            cash_amount=contract.cash_amount,
            currency=contract.currency,
            new_symbol=contract.new_symbol,
            source=contract.source,
            ingested_at=contract.ingested_at,
            meta=contract.metadata,
        )

    @staticmethod
    def to_contract(row: CorporateAction) -> CorporateActionContract:
        return CorporateActionContract(
            action_id=row.action_id,
            symbol=row.symbol,
            action_type=row.action_type,
            effective_date=row.effective_date,
            announced_date=row.announced_date,
            record_date=row.record_date,
            payable_date=row.payable_date,
            split_ratio=Decimal(str(row.split_ratio)) if row.split_ratio is not None else None,
            cash_amount=row.cash_amount,
            currency=row.currency,
            new_symbol=row.new_symbol,
            source=row.source,
            ingested_at=row.ingested_at,
            metadata=row.meta,
        )

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

    def list_by_symbol(self, *, symbol: str, limit: int = 50) -> list[CorporateAction]:
        stmt = (
            select(CorporateAction)
            .where(CorporateAction.symbol == symbol)
            .order_by(CorporateAction.effective_date.desc(), CorporateAction.ingested_at.desc())
            .limit(limit)
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

    def get_by_natural_key(
        self,
        *,
        symbol: str,
        action_type: object,
        effective_date: date,
        source: str,
    ) -> CorporateAction | None:
        """Fetch by the unique (symbol, type, effective_date, source) key."""
        stmt = select(CorporateAction).where(
            CorporateAction.symbol == symbol,
            CorporateAction.action_type == action_type,
            CorporateAction.effective_date == effective_date,
            CorporateAction.source == source,
        )
        result: CorporateAction | None = self.session.execute(stmt).scalar_one_or_none()
        return result

    def upsert(
        self, row: CorporateAction | CorporateActionContract
    ) -> UpsertResult[CorporateAction]:
        """Insert or update by action_id (then by natural key). Accepts the Pydantic
        contract the ingestion service produces or an ORM row."""
        if isinstance(row, CorporateActionContract):
            row = self.to_row(row)
        existing = self.get_by_action_id(row.action_id)
        if existing is None:
            # The provider may re-issue the same event under a new id; the natural key
            # is unique, so update that row instead of violating the constraint.
            existing = self.get_by_natural_key(
                symbol=row.symbol,
                action_type=row.action_type,
                effective_date=row.effective_date,
                source=row.source,
            )

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

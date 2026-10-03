from __future__ import annotations

from datetime import date
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select

from autonomous_trading_platform.contracts.accounting.corporate_action_application import (
    CorporateActionApplication,
    CorporateActionBook,
)
from autonomous_trading_platform.storage.sor.models.corporate_action_applications import (
    CorporateActionApplicationRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


def application_id_for(action_id: str, book: CorporateActionBook | str, scope: str) -> UUID:
    """Deterministic id per (action, book, scope): the same application maps to one row."""
    book_value = book.value if isinstance(book, CorporateActionBook) else str(book)
    return uuid5(NAMESPACE_URL, f"corporate-action-application:{action_id}:{book_value}:{scope}")


class CorporateActionApplicationRepository(BaseRepository):
    @staticmethod
    def to_row(contract: CorporateActionApplication) -> CorporateActionApplicationRow:
        return CorporateActionApplicationRow(
            application_id=contract.application_id,
            action_id=contract.action_id,
            book=contract.book.value,
            scope=contract.scope,
            symbol=contract.symbol,
            action_type=contract.action_type,
            effective_date=contract.effective_date,
            applied_at=contract.applied_at,
            quantity_before=contract.quantity_before,
            quantity_after=contract.quantity_after,
            avg_cost_before=contract.avg_cost_before,
            avg_cost_after=contract.avg_cost_after,
            cash_delta=contract.cash_delta,
            realized_pnl=contract.realized_pnl,
            run_id=contract.run_id,
            details=contract.details,
        )

    @staticmethod
    def to_contract(row: CorporateActionApplicationRow) -> CorporateActionApplication:
        return CorporateActionApplication(
            application_id=row.application_id,
            action_id=row.action_id,
            book=CorporateActionBook(row.book),
            scope=row.scope,
            symbol=row.symbol,
            action_type=row.action_type,
            effective_date=row.effective_date,
            applied_at=row.applied_at,
            quantity_before=row.quantity_before,
            quantity_after=row.quantity_after,
            avg_cost_before=row.avg_cost_before,
            avg_cost_after=row.avg_cost_after,
            cash_delta=row.cash_delta,
            realized_pnl=row.realized_pnl,
            run_id=row.run_id,
            details=row.details,
        )

    def get(
        self, *, action_id: str, book: CorporateActionBook | str, scope: str
    ) -> CorporateActionApplicationRow | None:
        return cast(
            CorporateActionApplicationRow | None,
            self.session.get(
                CorporateActionApplicationRow, application_id_for(action_id, book, scope)
            ),
        )

    def is_applied(self, *, action_id: str, book: CorporateActionBook | str, scope: str) -> bool:
        return self.get(action_id=action_id, book=book, scope=scope) is not None

    def applied_action_ids(
        self, *, book: CorporateActionBook | str, scope: str, action_ids: list[str]
    ) -> set[str]:
        if not action_ids:
            return set()
        book_value = book.value if isinstance(book, CorporateActionBook) else str(book)
        stmt = select(CorporateActionApplicationRow.action_id).where(
            CorporateActionApplicationRow.book == book_value,
            CorporateActionApplicationRow.scope == scope,
            CorporateActionApplicationRow.action_id.in_(action_ids),
        )
        return {str(value) for value in self.session.execute(stmt).scalars().all()}

    def record(self, application: CorporateActionApplication) -> CorporateActionApplicationRow:
        """Insert the application; returns the existing row when it was already recorded."""
        existing = cast(
            CorporateActionApplicationRow | None,
            self.session.get(CorporateActionApplicationRow, application.application_id),
        )
        if existing is not None:
            return existing
        row = self.to_row(application)
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_symbol(
        self, *, symbol: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[CorporateActionApplicationRow]:
        stmt = select(CorporateActionApplicationRow).where(
            CorporateActionApplicationRow.symbol == symbol
        )
        if start_date is not None:
            stmt = stmt.where(CorporateActionApplicationRow.effective_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(CorporateActionApplicationRow.effective_date <= end_date)
        stmt = stmt.order_by(
            CorporateActionApplicationRow.effective_date.asc(),
            CorporateActionApplicationRow.applied_at.asc(),
        )
        return cast(list[CorporateActionApplicationRow], self.session.execute(stmt).scalars().all())

    def list_applied(self) -> list[CorporateActionApplicationRow]:
        """Rows that changed a book (skipped resolutions carry details.resolution == "skipped")."""
        return [
            row for row in self.list_all() if (row.details or {}).get("resolution") != "skipped"
        ]

    def list_all(self) -> list[CorporateActionApplicationRow]:
        stmt = select(CorporateActionApplicationRow).order_by(
            CorporateActionApplicationRow.applied_at.asc()
        )
        return cast(list[CorporateActionApplicationRow], self.session.execute(stmt).scalars().all())

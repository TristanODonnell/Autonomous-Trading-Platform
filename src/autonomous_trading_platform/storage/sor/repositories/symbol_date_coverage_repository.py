from datetime import date
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.symbol_date_coverage import (
    SymbolDateCoverage,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class SymbolDateCoverageRepository(BaseRepository):
    def get_by_coverage_id(self, coverage_id: str) -> SymbolDateCoverage | None:
        stmt = select(SymbolDateCoverage).where(SymbolDateCoverage.coverage_id == coverage_id)
        return cast(SymbolDateCoverage | None, self.session.scalars(stmt).one_or_none())

    def insert(self, row: SymbolDateCoverage) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[SymbolDateCoverage]) -> None:
        self.session.add_all(rows)

    def upsert(self, row: SymbolDateCoverage) -> SymbolDateCoverage:
        existing = self.get_by_coverage_id(row.coverage_id)

        if existing is None:
            self.session.add(row)
            return row

        for column in SymbolDateCoverage.__table__.columns:
            setattr(existing, column.name, getattr(row, column.name))

        return existing

    def delete_by_coverage_id(self, coverage_id: str) -> None:
        obj = self.get_by_coverage_id(coverage_id)
        if obj is not None:
            self.session.delete(obj)

    def list_dataset_versions_covering_symbol_date_range(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[str]:
        stmt = (
            select(SymbolDateCoverage.dataset_version)
            .where(
                SymbolDateCoverage.symbol == symbol,
                SymbolDateCoverage.date >= start_date,
                SymbolDateCoverage.date <= end_date,
                SymbolDateCoverage.completeness_status == "complete",
            )
            .distinct()
        )
        return list(self.session.scalars(stmt).all())

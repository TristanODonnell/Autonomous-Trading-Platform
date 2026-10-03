from collections.abc import Iterable
from datetime import date
from typing import cast

from sqlalchemy import func, select

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

    def list_by_dataset_version_and_symbol(
        self,
        dataset_version: str,
        symbol: str,
    ) -> list[SymbolDateCoverage]:
        stmt = (
            select(SymbolDateCoverage)
            .where(
                SymbolDateCoverage.dataset_version == dataset_version,
                SymbolDateCoverage.symbol == symbol,
            )
            .order_by(SymbolDateCoverage.date.asc())
        )
        return list(self.session.scalars(stmt).all())

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

    def find_dataset_version_with_widest_coverage(
        self,
        *,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
    ) -> str | None:
        """Return the dataset_version covering the most distinct symbols
        (from `symbols`) with complete data in [start_date, end_date].

        Used to resolve which Parquet dataset_version actually holds
        already-ingested historical bars for a given symbol/date window,
        since coverage is tracked per dataset_version rather than globally.
        Returns None if no dataset_version has any complete coverage.
        """
        symbol_list = list({s for s in symbols if s})
        if not symbol_list:
            return None

        stmt = (
            select(
                SymbolDateCoverage.dataset_version,
                func.count(func.distinct(SymbolDateCoverage.symbol)),
            )
            .where(
                SymbolDateCoverage.symbol.in_(symbol_list),
                SymbolDateCoverage.date >= start_date,
                SymbolDateCoverage.date <= end_date,
                SymbolDateCoverage.completeness_status == "complete",
            )
            .group_by(SymbolDateCoverage.dataset_version)
            .order_by(func.count(func.distinct(SymbolDateCoverage.symbol)).desc())
        )
        row = self.session.execute(stmt).first()
        return cast(str | None, row[0]) if row is not None else None

    def last_dates_with_bars(
        self,
        *,
        dataset_version: str,
        symbols: Iterable[str],
    ) -> dict[str, date]:
        """Latest date each symbol had at least one bar in dataset_version.

        Symbols that never had a bar are absent from the result.
        """
        symbol_list = list({s for s in symbols if s})
        if not symbol_list:
            return {}
        stmt = (
            select(SymbolDateCoverage.symbol, func.max(SymbolDateCoverage.date))
            .where(
                SymbolDateCoverage.dataset_version == dataset_version,
                SymbolDateCoverage.symbol.in_(symbol_list),
                SymbolDateCoverage.actual_bar_count > 0,
            )
            .group_by(SymbolDateCoverage.symbol)
        )
        return {symbol: last for symbol, last in self.session.execute(stmt).all()}

    def dates_with_any_bars(
        self,
        *,
        dataset_version: str,
        since: date,
    ) -> list[date]:
        """Sorted dates after `since` on which any symbol had a bar (market-open days)."""
        stmt = (
            select(SymbolDateCoverage.date)
            .where(
                SymbolDateCoverage.dataset_version == dataset_version,
                SymbolDateCoverage.date > since,
                SymbolDateCoverage.actual_bar_count > 0,
            )
            .distinct()
            .order_by(SymbolDateCoverage.date.asc())
        )
        return list(self.session.scalars(stmt).all())

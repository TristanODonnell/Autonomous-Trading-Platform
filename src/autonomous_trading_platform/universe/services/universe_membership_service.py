from __future__ import annotations

from datetime import UTC, date, datetime, time

from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)


class UniverseMembershipService:
    def __init__(self, repository: UniverseSnapshotRepository) -> None:
        self.repository = repository

    def get_symbols_for_snapshot_date(self, snapshot_date: date) -> list[str]:
        snapshot = self.repository.get_by_snapshot_date(snapshot_date)
        if snapshot is None:
            return []
        return list(snapshot.symbols)

    def is_symbol_in_snapshot(self, symbol: str, snapshot_date: date) -> bool:
        snapshot = self.repository.get_by_snapshot_date(snapshot_date)
        if snapshot is None:
            return False
        return symbol in snapshot.symbols

    def get_symbols_for_date(self, as_of: date) -> list[str]:
        as_of_dt = datetime.combine(as_of, time.min, tzinfo=UTC)
        snapshot = self.repository.get_effective_for_date(as_of_dt)
        if snapshot is None:
            return []
        return list(snapshot.symbols)

    def is_symbol_active_on_date(self, symbol: str, as_of: date) -> bool:
        as_of_dt = datetime.combine(as_of, time.min, tzinfo=UTC)
        snapshot = self.repository.get_effective_for_date(as_of_dt)
        if snapshot is None:
            return False
        return symbol in snapshot.symbols

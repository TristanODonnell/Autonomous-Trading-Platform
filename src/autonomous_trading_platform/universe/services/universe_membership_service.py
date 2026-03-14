from __future__ import annotations

from datetime import date

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
        symbols = list(snapshot.symbols)
        return symbol in symbols

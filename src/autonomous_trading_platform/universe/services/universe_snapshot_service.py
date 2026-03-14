from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from autonomous_trading_platform.storage.sor.models.universe_snapshots import (
    UniverseSnapshot,
)
from autonomous_trading_platform.storage.sor.repositories.universe_snapshot_repository import (
    UniverseSnapshotRepository,
)


class UniverseSnapshotService:
    def __init__(self, repository: UniverseSnapshotRepository) -> None:
        self.repository = repository

    def build_snapshot(
        self,
        *,
        snapshot_date: date,
        effective_start: datetime,
        symbols: list[str],
        criteria: dict[str, Any],
        source: str,
        notes: str | None = None,
    ) -> UniverseSnapshot:
        version = self._compute_version(symbols)

        return UniverseSnapshot(
            universe_id=str(uuid.uuid4()),
            snapshot_date=snapshot_date,
            effective_start=self._ensure_utc(effective_start),
            effective_end=None,
            symbols=sorted(symbols),
            criteria=criteria,
            version=version,
            source=source,
            built_at=datetime.now(UTC),
            notes=notes,
        )

    def save_snapshot(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        return self.repository.upsert(snapshot)

    def build_and_save_snapshot(
        self,
        *,
        snapshot_date: date,
        effective_start: datetime,
        symbols: list[str],
        criteria: dict[str, Any],
        source: str,
        notes: str | None = None,
    ) -> UniverseSnapshot:
        snapshot = self.build_snapshot(
            snapshot_date=snapshot_date,
            effective_start=effective_start,
            symbols=symbols,
            criteria=criteria,
            source=source,
            notes=notes,
        )
        self.repository.close_open_snapshot(snapshot.effective_start)

        return self.repository.upsert(snapshot)

    def _compute_version(self, symbols: list[str]) -> str:
        payload = json.dumps(sorted(symbols), separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

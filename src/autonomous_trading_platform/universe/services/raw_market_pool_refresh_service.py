from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from typing import Protocol

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership as RawMarketPoolMembershipOrm,
)
from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolSnapshot as RawMarketPoolSnapshotOrm,
)
from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketSymbol as RawMarketSymbolOrm,
)
from autonomous_trading_platform.universe.types import RawSymbolProvider, RawSymbolRecord


@dataclass(frozen=True)
class RawMarketPoolRefreshPreview:
    captured_at: datetime
    source: str
    cadence: str
    symbol_count: int
    previous_snapshot_id: str | None
    previous_symbol_count: int
    new_symbols: list[str]
    delisted_symbols: list[str]
    symbols_preview: list[str]


class RawMarketPoolWriter(Protocol):
    def get_latest_complete_snapshot(self) -> RawMarketPoolSnapshotOrm | None: ...

    def get_symbols_for_snapshot(self, snapshot_id: str) -> list[str]: ...

    def insert_snapshot(self, snapshot: RawMarketPoolSnapshotOrm) -> None: ...

    def insert_memberships(self, memberships: list[RawMarketPoolMembershipOrm]) -> None: ...

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbolOrm | None: ...

    def upsert_raw_market_symbol(self, row: RawMarketSymbolOrm) -> None: ...

    def mark_snapshot_complete(self, snapshot_id: str) -> None: ...


class RawMarketPoolRefreshService:
    def __init__(
        self,
        repository: RawMarketPoolWriter,
        provider: RawSymbolProvider,
    ) -> None:
        self.repository = repository
        self.provider = provider

    def refresh(
        self,
        cadence: str,
        captured_at: datetime | None = None,
    ) -> tuple[RawMarketPoolSnapshotOrm, list[str], list[str]]:
        """
        Ingest a fresh raw symbol pool from the provider.

        Returns (snapshot, new_symbols, delisted_symbols).
        new_symbols: symbols appearing for the first time vs the previous snapshot.
        delisted_symbols: symbols in the previous snapshot absent from this one.
        """
        now = _ensure_utc(captured_at or datetime.now(UTC))

        raw_records = self.provider.fetch_symbols()
        normalized = self._normalize_and_deduplicate(raw_records)

        previous_snapshot = self.repository.get_latest_complete_snapshot()
        prev_symbols: set[str] = set()
        if previous_snapshot is not None:
            prev_symbols = set(
                self.repository.get_symbols_for_snapshot(previous_snapshot.snapshot_id)
            )

        current_symbols = {r.symbol for r in normalized}
        new_symbols = sorted(current_symbols - prev_symbols)
        delisted_symbols = sorted(prev_symbols - current_symbols)

        snapshot_id = str(uuid.uuid4())
        snapshot = RawMarketPoolSnapshotOrm(
            snapshot_id=snapshot_id,
            captured_at=now,
            source=self.provider.source_name,
            symbol_count=len(normalized),
            cadence=cadence,
            is_complete=False,
            metadata_json={
                "new_symbol_count": len(new_symbols),
                "delisted_symbol_count": len(delisted_symbols),
            },
        )
        self.repository.insert_snapshot(snapshot)

        memberships = [
            RawMarketPoolMembershipOrm(
                snapshot_id=snapshot_id,
                symbol=record.symbol,
                is_tradable=record.is_tradable,
                status=record.status,
                asset_type=record.asset_type,
                exchange=record.exchange,
            )
            for record in normalized
        ]
        self.repository.insert_memberships(memberships)

        for record in normalized:
            existing = self.repository.get_raw_market_symbol(record.symbol)
            first_seen = existing.first_seen if existing is not None else now
            self.repository.upsert_raw_market_symbol(
                RawMarketSymbolOrm(
                    symbol=record.symbol,
                    exchange=record.exchange,
                    asset_type=record.asset_type,
                    status=record.status,
                    is_tradable=record.is_tradable,
                    name=record.name,
                    currency=record.currency,
                    country=record.country,
                    marginable=record.marginable,
                    shortable=record.shortable,
                    fractionable=record.fractionable,
                    easy_to_borrow=record.easy_to_borrow,
                    provider_symbol=record.provider_symbol,
                    provider_asset_id=record.provider_asset_id,
                    source=self.provider.source_name,
                    first_seen=first_seen,
                    last_seen=now,
                    metadata_json=None,
                    created_at=first_seen,
                    updated_at=now,
                )
            )

        self.repository.mark_snapshot_complete(snapshot_id)
        snapshot.is_complete = True

        return snapshot, new_symbols, delisted_symbols

    def preview(
        self,
        cadence: str,
        captured_at: datetime | None = None,
    ) -> RawMarketPoolRefreshPreview:
        """
        Fetch and diff a raw symbol pool without writing snapshot, membership,
        or symbol rows.
        """
        now = _ensure_utc(captured_at or datetime.now(UTC))

        raw_records = self.provider.fetch_symbols()
        normalized = self._normalize_and_deduplicate(raw_records)

        previous_snapshot = self.repository.get_latest_complete_snapshot()
        prev_symbols: set[str] = set()
        if previous_snapshot is not None:
            prev_symbols = set(
                self.repository.get_symbols_for_snapshot(previous_snapshot.snapshot_id)
            )

        current_symbols = {r.symbol for r in normalized}
        new_symbols = sorted(current_symbols - prev_symbols)
        delisted_symbols = sorted(prev_symbols - current_symbols)

        return RawMarketPoolRefreshPreview(
            captured_at=now,
            source=self.provider.source_name,
            cadence=cadence,
            symbol_count=len(normalized),
            previous_snapshot_id=previous_snapshot.snapshot_id if previous_snapshot else None,
            previous_symbol_count=len(prev_symbols),
            new_symbols=new_symbols,
            delisted_symbols=delisted_symbols,
            symbols_preview=[record.symbol for record in normalized[:25]],
        )

    def detect_new_symbols(
        self,
        current_symbols: set[str],
        previous_symbols: set[str],
    ) -> list[str]:
        return sorted(current_symbols - previous_symbols)

    def detect_delisted_symbols(
        self,
        current_symbols: set[str],
        previous_symbols: set[str],
    ) -> list[str]:
        return sorted(previous_symbols - current_symbols)

    @staticmethod
    def _normalize_and_deduplicate(records: list[RawSymbolRecord]) -> list[RawSymbolRecord]:
        seen: dict[str, RawSymbolRecord] = {}
        for record in records:
            normalized_symbol = record.symbol.strip().upper()
            if not normalized_symbol:
                continue
            if normalized_symbol not in seen:
                seen[normalized_symbol] = dc_replace(record, symbol=normalized_symbol)
        return sorted(seen.values(), key=lambda r: r.symbol)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

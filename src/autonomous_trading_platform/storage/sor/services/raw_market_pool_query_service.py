from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership,
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)


class RawMarketPoolReader(Protocol):
    def get_latest_complete_snapshot(self) -> RawMarketPoolSnapshot | None: ...

    def get_latest_complete_snapshot_as_of(
        self, as_of: datetime
    ) -> RawMarketPoolSnapshot | None: ...

    def get_memberships_filtered(
        self,
        snapshot_id: str,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
        tradable_only: bool = False,
    ) -> list[RawMarketPoolMembership]: ...

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbol | None: ...


class RawMarketPoolQueryService:
    def __init__(self, repository: RawMarketPoolReader) -> None:
        self.repository = repository

    def get_latest_snapshot(self) -> RawMarketPoolSnapshot | None:
        return self.repository.get_latest_complete_snapshot()

    def get_snapshot_as_of(self, as_of: datetime) -> RawMarketPoolSnapshot | None:
        return self.repository.get_latest_complete_snapshot_as_of(_ensure_utc(as_of))

    def get_active_tradable_symbols(
        self,
        as_of: datetime | None = None,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
    ) -> list[str]:
        snapshot = (
            self.get_snapshot_as_of(as_of) if as_of is not None else self.get_latest_snapshot()
        )
        if snapshot is None:
            return []

        memberships = self.repository.get_memberships_filtered(
            snapshot.snapshot_id,
            asset_type=asset_type,
            exchange=exchange,
            tradable_only=True,
        )
        return sorted(m.symbol for m in memberships)

    def is_symbol_eligible(self, symbol: str, as_of: datetime | None = None) -> bool:
        return symbol in self.get_active_tradable_symbols(as_of)

    def get_symbol_count(self, as_of: datetime | None = None) -> int:
        return len(self.get_active_tradable_symbols(as_of))

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbol | None:
        return self.repository.get_raw_market_symbol(symbol)

    def get_memberships_for_snapshot(
        self,
        snapshot_id: str,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
        tradable_only: bool = False,
    ) -> list[RawMarketPoolMembership]:
        return self.repository.get_memberships_filtered(
            snapshot_id,
            asset_type=asset_type,
            exchange=exchange,
            tradable_only=tradable_only,
        )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

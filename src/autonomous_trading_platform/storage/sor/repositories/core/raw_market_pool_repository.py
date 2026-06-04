from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.storage.sor.models.raw_market_pool import (
    RawMarketPoolMembership,
    RawMarketPoolSnapshot,
    RawMarketSymbol,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class RawMarketPoolRepository(BaseRepository):
    def get_latest_complete_snapshot(self) -> RawMarketPoolSnapshot | None:
        stmt = (
            select(RawMarketPoolSnapshot)
            .where(RawMarketPoolSnapshot.is_complete.is_(True))
            .order_by(RawMarketPoolSnapshot.captured_at.desc())
        )
        return cast(
            RawMarketPoolSnapshot | None,
            self.session.execute(stmt).scalars().first(),
        )

    def get_latest_complete_snapshot_as_of(self, as_of: datetime) -> RawMarketPoolSnapshot | None:
        as_of_utc = _ensure_utc(as_of)
        stmt = (
            select(RawMarketPoolSnapshot)
            .where(
                RawMarketPoolSnapshot.is_complete.is_(True),
                RawMarketPoolSnapshot.captured_at <= as_of_utc,
            )
            .order_by(RawMarketPoolSnapshot.captured_at.desc())
        )
        return cast(
            RawMarketPoolSnapshot | None,
            self.session.execute(stmt).scalars().first(),
        )

    def get_snapshot_by_id(self, snapshot_id: str) -> RawMarketPoolSnapshot | None:
        stmt = select(RawMarketPoolSnapshot).where(RawMarketPoolSnapshot.snapshot_id == snapshot_id)
        return cast(
            RawMarketPoolSnapshot | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def get_recent_snapshots(self, limit: int = 20) -> list[RawMarketPoolSnapshot]:
        stmt = (
            select(RawMarketPoolSnapshot)
            .order_by(RawMarketPoolSnapshot.captured_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_memberships(self, snapshot_id: str) -> list[RawMarketPoolMembership]:
        stmt = (
            select(RawMarketPoolMembership)
            .where(RawMarketPoolMembership.snapshot_id == snapshot_id)
            .order_by(RawMarketPoolMembership.symbol.asc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_memberships_filtered(
        self,
        snapshot_id: str,
        *,
        asset_type: str | None = None,
        exchange: str | None = None,
        tradable_only: bool = False,
    ) -> list[RawMarketPoolMembership]:
        stmt = select(RawMarketPoolMembership).where(
            RawMarketPoolMembership.snapshot_id == snapshot_id
        )
        if asset_type is not None:
            stmt = stmt.where(RawMarketPoolMembership.asset_type == asset_type)
        if exchange is not None:
            stmt = stmt.where(RawMarketPoolMembership.exchange == exchange)
        if tradable_only:
            stmt = stmt.where(RawMarketPoolMembership.is_tradable.is_(True))
        stmt = stmt.order_by(RawMarketPoolMembership.symbol.asc())
        return list(self.session.execute(stmt).scalars().all())

    def get_symbols_for_snapshot(self, snapshot_id: str) -> list[str]:
        return [m.symbol for m in self.get_memberships(snapshot_id)]

    def get_raw_market_symbol(self, symbol: str) -> RawMarketSymbol | None:
        stmt = select(RawMarketSymbol).where(RawMarketSymbol.symbol == symbol)
        return cast(
            RawMarketSymbol | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def get_all_raw_market_symbols(self) -> list[RawMarketSymbol]:
        stmt = select(RawMarketSymbol).order_by(RawMarketSymbol.symbol.asc())
        return list(self.session.execute(stmt).scalars().all())

    def insert_snapshot(self, snapshot: RawMarketPoolSnapshot) -> None:
        self.session.add(snapshot)

    def insert_memberships(self, memberships: list[RawMarketPoolMembership]) -> None:
        if memberships:
            self.session.add_all(memberships)

    def mark_snapshot_complete(self, snapshot_id: str) -> None:
        snapshot = self.get_snapshot_by_id(snapshot_id)
        if snapshot is not None:
            snapshot.is_complete = True

    def upsert_raw_market_symbol(self, row: RawMarketSymbol) -> None:
        existing = self.get_raw_market_symbol(row.symbol)
        if existing is None:
            self.session.add(row)
        else:
            existing.exchange = row.exchange
            existing.asset_type = row.asset_type
            existing.status = row.status
            existing.is_tradable = row.is_tradable
            existing.name = row.name
            existing.currency = row.currency
            existing.country = row.country
            existing.marginable = row.marginable
            existing.shortable = row.shortable
            existing.fractionable = row.fractionable
            existing.easy_to_borrow = row.easy_to_borrow
            existing.provider_symbol = row.provider_symbol
            existing.provider_asset_id = row.provider_asset_id
            existing.source = row.source
            existing.last_seen = row.last_seen
            existing.metadata_json = row.metadata_json
            existing.updated_at = row.updated_at


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

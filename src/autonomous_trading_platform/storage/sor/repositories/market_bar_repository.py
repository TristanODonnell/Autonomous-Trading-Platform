from datetime import UTC, date, datetime, time
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class MarketBarRepository(BaseRepository):
    """
    Repository for interacting with the <table_name> table.

    Handles reads, writes, and idempotent upserts for <ModelName>.
    """

    # -----------------------------
    # Basic lookup
    # -----------------------------

    def get_by_bar_id(self, id_value: str) -> MarketBar | None:
        stmt = select(MarketBar).where(MarketBar.bar_id == id_value)
        return cast(MarketBar | None, self.session.scalars(stmt).one_or_none())

    def get_by_symbol_timestamp(
        self,
        *,
        symbol: str,
        timestamp: datetime,
    ) -> MarketBar | None:
        stmt = select(MarketBar).where(
            MarketBar.symbol == symbol,
            MarketBar.timestamp == timestamp,
        )
        return cast(MarketBar | None, self.session.scalars(stmt).one_or_none())

    def get_raw_bars_before_date(
        self,
        *,
        symbol: str,
        effective_date: date,
    ) -> list[MarketBar]:
        cutoff = datetime.combine(effective_date, time.min, tzinfo=UTC)

        stmt = (
            select(MarketBar)
            .where(
                MarketBar.symbol == symbol,
                MarketBar.timestamp < cutoff,
                MarketBar.price_basis == PriceBasis.RAW,
            )
            .order_by(MarketBar.timestamp.asc())
        )

        rows: list[MarketBar] = list(self.session.execute(stmt).scalars().all())
        return rows

    def get_bars_for_symbols_between(
        self,
        *,
        symbols: list[str],
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[MarketBar]:
        if not symbols:
            return []

        stmt = (
            select(MarketBar)
            .where(
                MarketBar.symbol.in_(symbols),
                MarketBar.timestamp >= start_ts,
                MarketBar.timestamp <= end_ts,
            )
            .order_by(MarketBar.timestamp.asc(), MarketBar.symbol.asc())
        )

        rows = self.session.execute(stmt).scalars().all()
        return cast(list[MarketBar], rows)

    # -----------------------------
    # Inserts
    # -----------------------------

    def insert(self, row: MarketBar) -> None:
        """Insert a single row."""
        self.session.add(row)

    def insert_many(self, rows: list[MarketBar]) -> None:
        """Insert multiple rows."""
        self.session.add_all(rows)

    # -----------------------------
    # Upserts
    # -----------------------------

    def upsert(self, row: MarketBar) -> MarketBar:
        """
        Insert or update based on deterministic ID.
        """
        existing = self.get_by_bar_id(row.bar_id)

        if existing is None:
            self.session.add(row)
            self.session.flush()
            return row

        existing.timestamp = row.timestamp
        existing.end_timestamp = row.end_timestamp
        existing.interval = row.interval
        existing.symbol = row.symbol
        existing.open = row.open
        existing.high = row.high
        existing.low = row.low
        existing.close = row.close
        existing.volume = row.volume
        existing.vwap = row.vwap
        existing.trade_count = row.trade_count
        existing.price_basis = row.price_basis
        existing.adjustment_factor = row.adjustment_factor
        existing.source = row.source
        existing.ingested_at = row.ingested_at
        existing.quality_flags = row.quality_flags
        existing.market_session = row.market_session
        self.session.flush()
        return existing

    # -----------------------------
    # Deletes (optional)
    # -----------------------------

    def delete_by_bar_id(self, id_value: str) -> None:
        """Delete a row by ID."""
        obj = self.get_by_bar_id(id_value)
        if obj is not None:
            self.session.delete(obj)

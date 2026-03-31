from datetime import UTC, date, datetime, time
from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.market.market_bar import (
    MarketBar as MarketBarContract,
)
from autonomous_trading_platform.storage.sor.models.market_bars import (
    MarketBar as MarketBarRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class MarketBarRepository(BaseRepository):
    """
    Repository for interacting with the market_bars table.
    """

    def _to_row(self, bar: MarketBarContract) -> MarketBarRow:
        return MarketBarRow(
            bar_id=bar.bar_id,
            timestamp=bar.timestamp,
            end_timestamp=bar.end_timestamp,
            interval=bar.interval,
            symbol=bar.symbol,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            vwap=bar.vwap,
            trade_count=bar.trade_count,
            price_basis=bar.price_basis,
            adjustment_factor=float(bar.adjustment_factor),
            source=bar.source,
            ingested_at=bar.ingested_at,
            quality_flags=[flag.value for flag in bar.quality_flags] if bar.quality_flags else [],
            market_session=bar.session,
        )

    def to_contract(self, row: MarketBarRow) -> MarketBarContract:
        return MarketBarContract(
            bar_id=row.bar_id,
            timestamp=row.timestamp,
            end_timestamp=row.end_timestamp,
            interval=row.interval,
            symbol=row.symbol,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            vwap=row.vwap,
            trade_count=row.trade_count,
            price_basis=row.price_basis,
            adjustment_factor=row.adjustment_factor,
            source=row.source,
            ingested_at=row.ingested_at,
            quality_flags=[],
            session=row.market_session,
        )

    def to_contracts(self, rows: list[MarketBarRow]) -> list[MarketBarContract]:
        return [self.to_contract(row) for row in rows]

    def get_by_bar_id(self, id_value: str) -> MarketBarRow | None:
        stmt = select(MarketBarRow).where(MarketBarRow.bar_id == id_value)
        return cast(MarketBarRow | None, self.session.scalars(stmt).one_or_none())

    def get_by_symbol_timestamp(
        self,
        *,
        symbol: str,
        timestamp: datetime,
    ) -> MarketBarRow | None:
        stmt = select(MarketBarRow).where(
            MarketBarRow.symbol == symbol,
            MarketBarRow.timestamp == timestamp,
        )
        return cast(MarketBarRow | None, self.session.scalars(stmt).one_or_none())

    def get_raw_bars_before_date(
        self,
        *,
        symbol: str,
        effective_date: date,
    ) -> list[MarketBarRow]:
        cutoff = datetime.combine(effective_date, time.min, tzinfo=UTC)

        stmt = (
            select(MarketBarRow)
            .where(
                MarketBarRow.symbol == symbol,
                MarketBarRow.timestamp < cutoff,
                MarketBarRow.price_basis == PriceBasis.RAW,
            )
            .order_by(MarketBarRow.timestamp.asc())
        )

        rows: list[MarketBarRow] = list(self.session.execute(stmt).scalars().all())
        return rows

    def get_bars_for_symbols_between(
        self,
        *,
        symbols: list[str],
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[MarketBarRow]:
        if not symbols:
            return []

        stmt = (
            select(MarketBarRow)
            .where(
                MarketBarRow.symbol.in_(symbols),
                MarketBarRow.timestamp >= start_ts,
                MarketBarRow.timestamp <= end_ts,
            )
            .order_by(MarketBarRow.timestamp.asc(), MarketBarRow.symbol.asc())
        )

        rows = self.session.execute(stmt).scalars().all()
        return cast(list[MarketBarRow], rows)

    def insert(self, row: MarketBarRow) -> None:
        self.session.add(row)

    def insert_many(self, rows: list[MarketBarRow]) -> None:
        self.session.add_all(rows)

    def upsert(self, bar: MarketBarContract) -> MarketBarRow:
        existing = self.get_by_bar_id(bar.bar_id)

        if existing is None:
            row = self._to_row(bar)
            self.session.add(row)
            self.session.flush()
            return row

        existing.timestamp = bar.timestamp
        existing.end_timestamp = bar.end_timestamp
        existing.interval = bar.interval
        existing.symbol = bar.symbol
        existing.open = bar.open
        existing.high = bar.high
        existing.low = bar.low
        existing.close = bar.close
        existing.volume = bar.volume
        existing.vwap = bar.vwap
        existing.trade_count = bar.trade_count
        existing.price_basis = bar.price_basis
        existing.adjustment_factor = float(bar.adjustment_factor)
        existing.source = bar.source
        existing.ingested_at = bar.ingested_at
        existing.quality_flags = (
            [flag.value for flag in bar.quality_flags] if bar.quality_flags else []
        )
        existing.market_session = bar.session
        self.session.flush()
        return existing

    def delete_by_bar_id(self, id_value: str) -> None:
        obj = self.get_by_bar_id(id_value)
        if obj is not None:
            self.session.delete(obj)

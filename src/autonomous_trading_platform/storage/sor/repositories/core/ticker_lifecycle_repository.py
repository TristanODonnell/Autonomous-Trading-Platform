from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEvent as TickerLifecycleEventContract,
)
from autonomous_trading_platform.storage.sor.models.ticker_lifecycle_event import (
    TickerLifecycleEvent as TickerLifecycleEventRow,
)


class TickerLifecycleRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        event: TickerLifecycleEventContract,
    ) -> TickerLifecycleEventContract:
        row = TickerLifecycleEventRow(
            event_id=event.event_id,
            symbol=event.symbol,
            event_type=event.event_type.value,
            effective_at=self._ensure_utc(event.effective_at),
            successor_symbol=event.successor_symbol,
            source=event.source,
            notes=event.notes,
        )
        merged = self.session.merge(row)
        self.session.commit()
        self.session.refresh(merged)
        return self._to_contract(merged)

    def list_events_for_symbol(
        self,
        symbol: str,
    ) -> list[TickerLifecycleEventContract]:
        stmt = (
            select(TickerLifecycleEventRow)
            .where(TickerLifecycleEventRow.symbol == symbol)
            .order_by(TickerLifecycleEventRow.effective_at.asc())
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._to_contract(row) for row in rows]

    def get_latest_event_for_symbol_as_of(
        self,
        symbol: str,
        as_of: datetime,
    ) -> TickerLifecycleEventContract | None:
        stmt = (
            select(TickerLifecycleEventRow)
            .where(
                TickerLifecycleEventRow.symbol == symbol,
                TickerLifecycleEventRow.effective_at <= self._ensure_utc(as_of),
            )
            .order_by(TickerLifecycleEventRow.effective_at.desc())
        )
        row = self.session.execute(stmt).scalars().first()
        if row is None:
            return None
        return self._to_contract(row)

    def _to_contract(
        self,
        row: TickerLifecycleEventRow,
    ) -> TickerLifecycleEventContract:
        return TickerLifecycleEventContract(
            event_id=row.event_id,
            symbol=row.symbol,
            event_type=row.event_type,
            effective_at=row.effective_at,
            successor_symbol=row.successor_symbol,
            source=row.source,
            notes=row.notes,
        )

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

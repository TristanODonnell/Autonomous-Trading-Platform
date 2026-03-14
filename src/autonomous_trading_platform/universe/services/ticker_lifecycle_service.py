from __future__ import annotations

from datetime import datetime

from autonomous_trading_platform.contracts.runtime.ticker_lifecycle_event import (
    TickerLifecycleEventType,
)
from autonomous_trading_platform.storage.sor.repositories.ticker_lifecycle_repository import (
    TickerLifecycleRepository,
)


class TickerLifecycleService:
    def __init__(self, repository: TickerLifecycleRepository) -> None:
        self.repository = repository

    def get_successor_symbol(self, symbol: str, as_of: datetime) -> str | None:
        event = self.repository.get_latest_event_for_symbol_as_of(symbol, as_of)
        if event is None:
            return None

        if event.event_type in {
            TickerLifecycleEventType.RENAME,
            TickerLifecycleEventType.SUCCESSOR,
            TickerLifecycleEventType.MERGER,
        }:
            return event.successor_symbol

        return None

    def is_delisted(self, symbol: str, as_of: datetime) -> bool:
        event = self.repository.get_latest_event_for_symbol_as_of(symbol, as_of)
        if event is None:
            return False

        return event.event_type == TickerLifecycleEventType.DELISTING

    def resolve_symbol(self, symbol: str, as_of: datetime) -> str:
        successor = self.get_successor_symbol(symbol, as_of)
        if successor:
            return successor
        return symbol

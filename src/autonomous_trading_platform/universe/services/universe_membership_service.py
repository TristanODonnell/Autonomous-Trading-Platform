from __future__ import annotations

from datetime import UTC, date, datetime, time

from autonomous_trading_platform.storage.sor.repositories.core.universe_version_repository import (
    UniverseVersionRepository,
)
from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)


class UniverseMembershipService:
    def __init__(
        self,
        repository: UniverseVersionRepository,
        ticker_lifecycle_service: TickerLifecycleService | None = None,
    ) -> None:
        self.repository = repository
        self.ticker_lifecycle_service = ticker_lifecycle_service

    def get_symbols_for_date(self, as_of: date) -> list[str]:
        as_of_dt = datetime.combine(as_of, time.min, tzinfo=UTC)
        version = self.repository.get_active_version(as_of_dt)
        if version is None:
            return []
        return self.repository.get_symbols(version.universe_version_id)

    def get_resolved_symbols_for_date(self, as_of: date) -> list[str]:
        raw_symbols = self.get_symbols_for_date(as_of)

        if self.ticker_lifecycle_service is None:
            return raw_symbols

        as_of_dt = datetime.combine(as_of, time.min, tzinfo=UTC)
        resolved = [
            self.ticker_lifecycle_service.resolve_symbol(symbol, as_of_dt) for symbol in raw_symbols
        ]
        return sorted(set(resolved))

    def is_symbol_in_universe_on_date(self, symbol: str, as_of: date) -> bool:
        return symbol in self.get_symbols_for_date(as_of)

    def is_resolved_symbol_in_universe_on_date(self, symbol: str, as_of: date) -> bool:
        return symbol in self.get_resolved_symbols_for_date(as_of)

    def get_query_symbols_for_date(self, evaluation_date: date) -> list[str]:
        if self.ticker_lifecycle_service is None:
            return sorted(set(self.get_symbols_for_date(evaluation_date)))
        return self.get_resolved_symbols_for_date(evaluation_date)

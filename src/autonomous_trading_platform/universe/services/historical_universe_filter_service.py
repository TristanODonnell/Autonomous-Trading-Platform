from __future__ import annotations

from datetime import UTC, date, datetime, time

from autonomous_trading_platform.universe.services.ticker_lifecycle_service import (
    TickerLifecycleService,
)
from autonomous_trading_platform.universe.services.universe_membership_service import (
    UniverseMembershipService,
)


class HistoricalUniverseFilterService:
    def __init__(
        self,
        membership_service: UniverseMembershipService,
        ticker_lifecycle_service: TickerLifecycleService | None = None,
    ) -> None:
        self.membership_service = membership_service
        self.ticker_lifecycle_service = ticker_lifecycle_service

    def get_query_symbols_for_date(self, evaluation_date: date) -> list[str]:
        symbols = self.membership_service.get_symbols_for_date(evaluation_date)

        if self.ticker_lifecycle_service is None:
            return sorted(set(symbols))

        evaluation_dt = datetime.combine(evaluation_date, time.min, tzinfo=UTC)

        resolved: list[str] = []
        for symbol in symbols:
            resolved.append(self.ticker_lifecycle_service.resolve_symbol(symbol, evaluation_dt))

        return sorted(set(resolved))

    def filter_symbols_for_date(
        self,
        symbols: list[str],
        evaluation_date: date,
    ) -> list[str]:
        active = set(self.get_query_symbols_for_date(evaluation_date))
        return [symbol for symbol in symbols if symbol in active]

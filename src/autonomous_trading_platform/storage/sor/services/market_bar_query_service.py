from datetime import date, datetime

from autonomous_trading_platform.storage.sor.models.market_bars import MarketBar
from autonomous_trading_platform.storage.sor.repositories.core.market_bar_repository import (
    MarketBarRepository,
)
from autonomous_trading_platform.universe.services.historical_universe_filter_service import (
    HistoricalUniverseFilterService,
)


class MarketBarQueryService:
    def __init__(
        self,
        market_bar_repository: MarketBarRepository,
        historical_universe_filter_service: HistoricalUniverseFilterService,
    ) -> None:
        self.market_bar_repository = market_bar_repository
        self.historical_universe_filter_service = historical_universe_filter_service

    def get_bars_for_universe_date(
        self,
        *,
        evaluation_date: date,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[MarketBar]:
        symbols = self.historical_universe_filter_service.get_query_symbols_for_date(
            evaluation_date
        )
        return self.market_bar_repository.get_bars_for_symbols_between(
            symbols=symbols,
            start_ts=start_ts,
            end_ts=end_ts,
        )

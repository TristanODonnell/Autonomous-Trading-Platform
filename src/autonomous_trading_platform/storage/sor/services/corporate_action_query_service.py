from datetime import date, datetime

from autonomous_trading_platform.storage.sor.models.corporate_actions import CorporateAction
from autonomous_trading_platform.storage.sor.repositories.core.corporate_action_repository import (
    CorporateActionRepository,
)
from autonomous_trading_platform.universe.services.historical_universe_filter_service import (
    HistoricalUniverseFilterService,
)


class CorporateActionQueryService:
    def __init__(
        self,
        corporate_action_repository: CorporateActionRepository,
        historical_universe_filter_service: HistoricalUniverseFilterService,
    ) -> None:
        self.corporate_action_repository = corporate_action_repository
        self.historical_universe_filter_service = historical_universe_filter_service

    def get_actions_for_universe_date(
        self,
        *,
        evaluation_date: date,
        start_ts: datetime,
        end_ts: datetime,
    ) -> list[CorporateAction]:
        symbols = self.historical_universe_filter_service.get_query_symbols_for_date(
            evaluation_date
        )
        return self.corporate_action_repository.get_actions_for_symbols_between(
            symbols=symbols,
            start_date=start_ts.date(),
            end_date=end_ts.date(),
        )

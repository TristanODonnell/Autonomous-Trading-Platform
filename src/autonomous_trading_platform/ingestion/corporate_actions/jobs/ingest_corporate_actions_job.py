from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.ingestion.corporate_actions.clients import (
    alpaca_corporate_action_client as client,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_adjustment_service import (
    CorporateActionAdjustmentService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_normalization_service import (
    CorporateActionNormalizationService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class IngestCorporateActionsJob:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ingest_corporate_actions_job(self) -> None:
        normalization_service = CorporateActionNormalizationService()
        adjustment_service = CorporateActionAdjustmentService()

        payload: dict = client.fetch_corporate_actions()

        raw_actions = payload.get("corporate_actions", [])

        parsed_actions: list[CorporateAction] = [
            normalization_service.parse_alpaca_corporate_action(raw_action)
            for raw_action in raw_actions
        ]

        with SorUnitOfWork(self.session) as uow:
            for action in parsed_actions:
                uow.corporate_actions.upsert(action)

                raw_bars = uow.market_bars.get_raw_bars_before_date(
                    symbol=action.symbol,
                    effective_date=action.effective_date,
                )

                adjusted_bars = adjustment_service.apply_action_to_bars(
                    action,
                    raw_bars,
                )

                for bar in adjusted_bars:
                    uow.market_bars.upsert(bar)

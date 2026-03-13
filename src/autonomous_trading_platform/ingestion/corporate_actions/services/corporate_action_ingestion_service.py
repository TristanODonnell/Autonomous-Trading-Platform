from __future__ import annotations

from sqlalchemy.orm import Session

from autonomous_trading_platform.ingestion.corporate_actions.clients import (
    alpaca_corporate_action_client as client,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_adjustment_service import (
    CorporateActionAdjustmentService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_normalization_service import (
    CorporateActionNormalizationService,
)
from autonomous_trading_platform.ingestion.corporate_actions.services.corporate_action_validation_service import (
    CorporateActionValidationService,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


class CorporateActionIngestionService:
    def __init__(self, session: Session):
        self.session = session
        self.normalization_service = CorporateActionNormalizationService()
        self.adjustment_service = CorporateActionAdjustmentService()
        self.validation_service = CorporateActionValidationService()

    def ingest_corporate_actions(self) -> None:
        payload: dict = client.fetch_corporate_actions()
        raw_actions = payload.get("corporate_actions", [])

        with SorUnitOfWork(self.session) as uow:
            for raw_action in raw_actions:
                action = self.normalization_service.parse_alpaca_corporate_action(raw_action)

                validation_result = self.validation_service.validate(action)
                if not validation_result.ok:
                    # later: log / persist audit / attach flags
                    continue

                uow.corporate_actions.upsert(action)

                if not self.adjustment_service.supports_adjustment(action):
                    continue

                raw_bars = uow.market_bars.get_raw_bars_before_date(
                    symbol=action.symbol,
                    effective_date=action.effective_date,
                )

                adjusted_bars = self.adjustment_service.apply_action_to_bars(
                    action,
                    raw_bars,
                )

                for bar in adjusted_bars:
                    uow.market_bars.upsert(bar)

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    def __init__(
        self,
        *,
        session: Session,
        run_id: str,
        audit_logger: Any,
        cycle_timestamp: datetime,
    ) -> None:
        self.session = session
        self.normalization_service: Any = CorporateActionNormalizationService()
        self.validation_service: Any = CorporateActionValidationService()
        self.adjustment_service: Any = CorporateActionAdjustmentService()
        self.run_id = run_id
        self.cycle_timestamp = cycle_timestamp
        self.audit_logger = audit_logger

    def ingest_corporate_actions(self) -> None:
        payload: dict = client.fetch_corporate_actions()
        actions_block = payload.get("corporate_actions", {})

        raw_actions = actions_block.get("cash_dividends", []) + actions_block.get(
            "reverse_splits", []
        )
        with SorUnitOfWork(self.session) as uow:
            for raw_action in raw_actions:
                try:
                    action = self.normalization_service.parse_alpaca_corporate_action(raw_action)
                except ValueError:
                    self.audit_logger.record_corporate_action_parse_failed(
                        run_id=self.run_id,
                        symbol=raw_action.get("symbol", "UNKNOWN"),
                        cycle_timestamp=self.cycle_timestamp,
                    )
                    continue

                validation_result = self.validation_service.validate(action)
                if not validation_result.ok:
                    self.audit_logger.record_corporate_action_validation_failed(
                        run_id=self.run_id,
                        symbol=action.symbol,
                        cycle_timestamp=self.cycle_timestamp,
                    )
                    continue

                result = uow.corporate_actions.upsert(action)

                if not result.created:
                    continue

                if not self.adjustment_service.supports_adjustment(action):
                    continue

                raw_bars = uow.market_bars.get_raw_bars_before_date(
                    symbol=action.symbol,
                    effective_date=action.effective_date,
                )

                raw_bar_contracts = uow.market_bars.to_contracts(raw_bars)

                adjusted_bars = self.adjustment_service.apply_action_to_bars(
                    action,
                    raw_bar_contracts,
                )

                self.audit_logger.record_corporate_action_adjustment_applied(
                    run_id=self.run_id,
                    symbol=action.symbol,
                    cycle_timestamp=self.cycle_timestamp,
                )
                for bar in adjusted_bars:
                    uow.market_bars.upsert(bar)
